"""Dock (loader), road (driver) and counter (store) endpoints, plus the offline sync endpoint.

Every write a phone or tablet makes can arrive later through POST /api/sync with the time it
happened; the direct endpoints are the same operations, online."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from ..deps import Ctx, driver, get_ctx, loader, store
from ..domain import fieldops
from ..domain.clock import clock_payload, now_virtual
from ..domain.events import post_notice, record
from ..domain.ordering import OrderingError, catalog_for, submit_basket
from ..domain.views import dock_queue, dock_trip, driver_run, store_home, store_order_view
from ..models import Event, Issue, Notice, Order, OrderLine, Trip

log = logging.getLogger("relay.sync")

router = APIRouter(prefix="/api", tags=["field"])


def _fail(exc: Exception) -> HTTPException:
    return HTTPException(409, str(exc))


# ------------------------------------------------------------------------------------------------
# dock
# ------------------------------------------------------------------------------------------------


@router.get("/dock/queue")
def dock_q(ctx: Ctx = Depends(loader)) -> dict:
    return dock_queue(ctx.db, ctx.ws, ctx.user)


@router.get("/dock/trips/{trip_id}")
def dock_t(trip_id: int, ctx: Ctx = Depends(loader)) -> dict:
    out = dock_trip(ctx.db, ctx.ws, ctx.user, trip_id)
    if not out:
        raise HTTPException(404, "Unknown trip")
    return out


class CheckIn(BaseModel):
    loaded: bool = True
    crew: str | None = None


@router.post("/dock/lines/{line_id}/check")
def dock_check(line_id: int, body: CheckIn, ctx: Ctx = Depends(loader)) -> dict:
    try:
        out = fieldops.check_line(ctx.db, ctx.ws, ctx.user, line_id, body.loaded, body.crew, None)
    except fieldops.FieldError as exc:
        raise _fail(exc) from exc
    ctx.db.commit()
    return out


class FlagIn(BaseModel):
    kind: str = Field(pattern="^(missing|damaged|wrong)$")
    qty: int = Field(ge=1, le=9999)
    note: str = Field(default="", max_length=300)
    photo: str | None = None
    crew: str | None = None


@router.post("/dock/lines/{line_id}/flag")
def dock_flag(line_id: int, body: FlagIn, ctx: Ctx = Depends(loader)) -> dict:
    try:
        out = fieldops.flag_line(ctx.db, ctx.ws, ctx.user, line_id, body.kind, body.qty, body.note, body.photo, body.crew, None)
    except fieldops.FieldError as exc:
        raise _fail(exc) from exc
    ctx.db.commit()
    return out


class ReleaseIn(BaseModel):
    temp_c: float | None = None
    seal: str = Field(min_length=1, max_length=20)
    doors_ok: bool = True
    photo: str | None = None
    crew: str | None = None


@router.post("/dock/trips/{trip_id}/release")
def dock_release(trip_id: int, body: ReleaseIn, ctx: Ctx = Depends(loader)) -> dict:
    try:
        out = fieldops.release_trip(ctx.db, ctx.ws, ctx.user, trip_id, body.temp_c, body.seal, body.doors_ok, body.photo, body.crew, None)
    except fieldops.FieldError as exc:
        raise _fail(exc) from exc
    ctx.db.commit()
    return out


@router.post("/dock/changes/ack")
def dock_ack(ctx: Ctx = Depends(loader)) -> dict:
    for n in ctx.db.query(Notice).filter_by(workspace_id=ctx.ws.id, audience=f"depot:{ctx.user.depot}", read=False):
        n.read = True
    ctx.db.commit()
    return {"ok": True}


class AckIn(BaseModel):
    crew: str | None = None


@router.post("/dock/changes/{notice_id}/ack")
def dock_ack_one(notice_id: int, body: AckIn, ctx: Ctx = Depends(loader)) -> dict:
    """The dock confirms one plan change ("It's off the truck", "Got it"). A take-off is reported
    to the dispatcher, because until then the goods are physically on the wrong vehicle."""
    n = ctx.db.get(Notice, notice_id)
    if not n or n.workspace_id != ctx.ws.id or n.audience != f"depot:{ctx.user.depot}":
        raise HTTPException(404, "Unknown change")
    who = body.crew or ctx.user.short_name
    n.read = True
    if (n.key or "").startswith("take:"):
        post_notice(
            ctx.db,
            ctx.ws,
            "dispatch",
            re.sub(r"^(Unseal and take|Take) off", "Taken off", n.title),
            f"{who} confirmed it is off the truck.",
            kind="green",
            icon="check",
        )
    record(
        ctx.db,
        ctx.ws,
        "dock.change.acked",
        actor_name=who,
        actor_role="loader",
        entity=f"notice:{n.id}",
        payload={"key": n.key, "title": n.title},
        topics=[f"depot:{ctx.user.depot}"],
    )
    ctx.db.commit()
    return {"ok": True}


@router.post("/dock/issues/{issue_id}/remind")
def dock_remind(issue_id: int, body: AckIn, ctx: Ctx = Depends(loader)) -> dict:
    """Nudge the dispatcher about a shortfall still waiting for a decision (moves it to the top of her feed)."""
    i = ctx.db.get(Issue, issue_id)
    if not i or i.workspace_id != ctx.ws.id:
        raise HTTPException(404, "Unknown issue")
    if i.status != "open":
        return {"ok": True, "already": True}
    trip = ctx.db.get(Trip, i.trip_id) if i.trip_id else None
    line = ctx.db.get(OrderLine, i.line_id) if i.line_id else None
    who = body.crew or ctx.user.short_name
    post_notice(
        ctx.db,
        ctx.ws,
        "dispatch",
        f"Dock is waiting for you · {trip.vehicle_id if trip else 'shortfall'}",
        f"{who} needs a decision on {i.qty} × {line.name if line else 'items'}."
        + (f" {trip.vehicle_id} departs {trip.planned_depart:%H:%M}." if trip else ""),
        kind="ember",
        icon="bell",
        key=f"issue:{i.id}",
        entity=f"issue:{i.id}",
        actions=[["Decide", f"shortfall:{i.id}"]] + ([["Locate", f"locate:{trip.code}"]] if trip else []),
    )
    record(ctx.db, ctx.ws, "issue.reminded", actor_name=who, actor_role="loader", entity=f"issue:{i.id}")
    ctx.db.commit()
    return {"ok": True}


# ------------------------------------------------------------------------------------------------
# driver
# ------------------------------------------------------------------------------------------------


@router.get("/driver/run")
def run(ctx: Ctx = Depends(driver)) -> dict:
    return driver_run(ctx.db, ctx.ws, ctx.user)


class HeartbeatIn(BaseModel):
    pending: int = 0


@router.post("/driver/heartbeat")
def hb(body: HeartbeatIn, ctx: Ctx = Depends(driver)) -> dict:
    out = fieldops.heartbeat(ctx.db, ctx.ws, ctx.user, body.pending)
    ctx.db.commit()
    return {**out, "clock": clock_payload(ctx.ws)}


@router.post("/driver/notices/read")
def d_read(ctx: Ctx = Depends(driver)) -> dict:
    for n in ctx.db.query(Notice).filter_by(workspace_id=ctx.ws.id, audience=f"vehicle:{ctx.user.vehicle_id}", read=False):
        n.read = True
    ctx.db.commit()
    return {"ok": True}


# ------------------------------------------------------------------------------------------------
# store
# ------------------------------------------------------------------------------------------------


@router.get("/store/home")
def s_home(ctx: Ctx = Depends(store)) -> dict:
    return store_home(ctx.db, ctx.ws, ctx.user)


@router.get("/store/catalog")
def s_catalog(ctx: Ctx = Depends(store)) -> dict:
    return catalog_for(ctx.db, ctx.ws, ctx.user.outlet_id)


class BasketIn(BaseModel):
    lines: dict[str, int]


@router.post("/store/orders")
def s_order(body: BasketIn, ctx: Ctx = Depends(store)) -> dict:
    try:
        orders = submit_basket(ctx.db, ctx.ws, ctx.user, body.lines)
    except OrderingError as exc:
        raise _fail(exc) from exc
    ctx.db.commit()
    return {
        "orders": [
            {
                "id": o.id,
                "ref": o.ref,
                "temp": o.temp,
                "units": o.units,
                "m3": round(o.volume_m3, 2),
                "kg": round(o.weight_kg),
                "service_date": o.service_date.isoformat(),
            }
            for o in orders
        ]
    }


@router.get("/store/orders/{order_id}")
def s_track(order_id: int, ctx: Ctx = Depends(store)) -> dict:
    o = ctx.db.get(Order, order_id)
    if not o or o.workspace_id != ctx.ws.id or o.outlet_id != ctx.user.outlet_id:
        raise HTTPException(404, "Unknown order")
    view = store_order_view(ctx.db, ctx.ws, o, now_virtual(ctx.ws))
    events = (
        ctx.db.query(Event).filter(Event.workspace_id == ctx.ws.id).filter(Event.entity.in_([f"order:{o.id}"])).order_by(Event.id).all()
    )
    view["events"] = [{"type": e.type, "at": e.virtual_at.isoformat(), "actor": e.actor_name} for e in events]
    return view


class ReceiptIn(BaseModel):
    lines: list[dict[str, Any]] = []
    note: str = Field(default="", max_length=300)


@router.post("/store/orders/{order_id}/receipt")
def s_receipt(order_id: int, body: ReceiptIn, ctx: Ctx = Depends(store)) -> dict:
    try:
        out = fieldops.confirm_receipt(ctx.db, ctx.ws, ctx.user, order_id, body.lines, body.note)
    except fieldops.FieldError as exc:
        raise _fail(exc) from exc
    ctx.db.commit()
    return out


@router.post("/store/notices/read")
def s_read(ctx: Ctx = Depends(store)) -> dict:
    for n in ctx.db.query(Notice).filter_by(workspace_id=ctx.ws.id, audience=f"outlet:{ctx.user.outlet_id}", read=False):
        n.read = True
    ctx.db.commit()
    return {"ok": True}


# ------------------------------------------------------------------------------------------------
# offline sync
# ------------------------------------------------------------------------------------------------


class SyncEvent(BaseModel):
    client_event_id: str = Field(min_length=8, max_length=64)
    type: str
    at: datetime | None = None  # virtual time on the device when it happened
    offline: bool = False
    payload: dict[str, Any] = {}


class SyncIn(BaseModel):
    device_id: str = Field(default="", max_length=32, pattern=r"^[A-Za-z0-9_-]*$")  # stored as "device:<id>" (40 chars)
    events: list[SyncEvent] = Field(default_factory=list, max_length=200)
    pending_after: int = Field(default=0, ge=0)  # records still on the device after this batch


HANDLERS = {
    "run.start": lambda db, ws, u, p, e: fieldops.start_run(db, ws, u, int(p["trip_id"]), e.at),
    "stop.arrive": lambda db, ws, u, p, e: fieldops.arrive(db, ws, u, [int(x) for x in p["stop_ids"]], e.at, e.offline),
    "stop.complete": lambda db, ws, u, p, e: fieldops.complete(
        db,
        ws,
        u,
        [int(x) for x in p["stop_ids"]],
        p.get("outcome", "delivered"),
        p.get("lines", []),
        p.get("receiver", ""),
        p.get("photo"),
        p.get("signature"),
        p.get("note", ""),
        e.at,
        e.offline,
    ),
    "report": lambda db, ws, u, p, e: fieldops.report_problem(
        db, ws, u, int(p["trip_id"]), p.get("stop_id"), p.get("kind", "other"), p.get("note", ""), p.get("photo"), e.at, e.offline
    ),
    "line.check": lambda db, ws, u, p, e: fieldops.check_line(
        db, ws, u, int(p["line_id"]), bool(p.get("loaded", True)), p.get("crew"), e.at
    ),
    "line.flag": lambda db, ws, u, p, e: fieldops.flag_line(
        db, ws, u, int(p["line_id"]), p["kind"], int(p["qty"]), p.get("note", ""), p.get("photo"), p.get("crew"), e.at
    ),
    "trip.release": lambda db, ws, u, p, e: fieldops.release_trip(
        db, ws, u, int(p["trip_id"]), p.get("temp_c"), p.get("seal", ""), bool(p.get("doors_ok", True)), p.get("photo"), p.get("crew"), e.at
    ),
}
ROLE_EVENTS = {"driver": {"run.start", "stop.arrive", "stop.complete", "report"}, "loader": {"line.check", "line.flag", "trip.release"}}


@router.post("/sync")
def sync(body: SyncIn, ctx: Ctx = Depends(get_ctx)) -> dict:
    """Apply a batch of offline events in order. Idempotent: an event id seen before returns its
    original result; a rejected event is reported, never retried forever."""
    db, ws, u = ctx.db, ctx.ws, ctx.user
    allowed = ROLE_EVENTS.get(u.role, set())
    results = []
    for e in body.events:
        prev = db.query(Event).filter_by(workspace_id=ws.id, client_event_id=e.client_event_id).first()
        if prev:
            rejected = (prev.result or {}).get("rejected") if isinstance(prev.result, dict) else None
            if rejected:
                # a resend of a refused record is still refused: the phone keeps showing why
                results.append({"client_event_id": e.client_event_id, "status": "rejected", "message": rejected})
            else:
                results.append({"client_event_id": e.client_event_id, "status": "duplicate", "result": prev.result})
            continue
        if e.type not in allowed:
            results.append({"client_event_id": e.client_event_id, "status": "rejected", "message": f"{u.role} cannot send {e.type}"})
            continue
        try:
            with db.begin_nested():
                out = HANDLERS[e.type](db, ws, u, e.payload, e)
                record(
                    db,
                    ws,
                    f"sync.{e.type}",
                    actor_name=u.short_name,
                    actor_role=u.role,
                    entity=f"device:{body.device_id}",
                    payload={"type": e.type},
                    client_event_id=e.client_event_id,
                    device_at=e.at,
                    result=out,
                )
            results.append({"client_event_id": e.client_event_id, "status": "applied", "result": out})
        except fieldops.FieldError as exc:
            msg = str(exc)
            if msg == "moved":
                msg = "The dispatcher moved this stop to another vehicle before your record arrived."
            with db.begin_nested():
                record(
                    db,
                    ws,
                    f"sync.rejected.{e.type}",
                    actor_name=u.short_name,
                    actor_role=u.role,
                    client_event_id=e.client_event_id,
                    device_at=e.at,
                    result={"rejected": msg},
                )
            results.append({"client_event_id": e.client_event_id, "status": "rejected", "message": msg})
        except (KeyError, ValueError, TypeError) as exc:
            results.append({"client_event_id": e.client_event_id, "status": "rejected", "message": f"Malformed event: {exc}"})
        except IntegrityError as exc:
            # only this event's savepoint was rolled back; events applied earlier in the batch stay
            if db.query(Event.id).filter_by(workspace_id=ws.id, client_event_id=e.client_event_id).first():
                results.append({"client_event_id": e.client_event_id, "status": "duplicate"})  # a concurrent resend won
            else:
                log.warning("sync %s rejected by the database: %s", e.type, exc.orig)
                msg = "This record no longer matches the plan, so it could not be saved."
                results.append({"client_event_id": e.client_event_id, "status": "rejected", "message": msg})
    changes = []
    if u.role == "driver":
        changes = fieldops.heartbeat(db, ws, u, body.pending_after)["changes"]
    db.commit()
    return {"results": results, "changes": changes, "clock": clock_payload(ws)}
