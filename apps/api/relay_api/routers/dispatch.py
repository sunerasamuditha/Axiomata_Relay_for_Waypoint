"""Dispatcher: the canvas snapshot, cutoff, planning, the deferral lever, moves, decisions, outlook."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..config import get_settings
from ..deps import Ctx, dispatcher
from ..domain import fieldops, planning
from ..domain.clock import now_virtual
from ..domain.events import post_notice, record, resolve_notices
from ..domain.views import dispatch_snapshot
from ..models import Issue, Notice, Order, Trip, VehicleDay

router = APIRouter(prefix="/api/dispatch", tags=["dispatcher"])


def _err(exc: Exception) -> HTTPException:
    return HTTPException(409, str(exc))


@router.get("/snapshot")
def snapshot(ctx: Ctx = Depends(dispatcher)) -> dict:
    return dispatch_snapshot(ctx.db, ctx.ws)


@router.post("/orders/close")
def close_orders(ctx: Ctx = Depends(dispatcher)) -> dict:
    try:
        out = planning.close_orders(ctx.db, ctx.ws, ctx.actor)
    except planning.PlanningError as exc:
        raise _err(exc) from exc
    ctx.db.commit()
    return out


class PlanIn(BaseModel):
    policy: str = Field(default="balanced", pattern="^(throughput|balanced|fairness)$")
    publish: bool = False


@router.post("/plans")
def make_plan(body: PlanIn, ctx: Ctx = Depends(dispatcher)) -> dict:
    db, ws = ctx.db, ctx.ws
    if not ws.orders_closed_at:
        try:
            planning.close_orders(db, ws, ctx.actor)
        except planning.PlanningError as exc:
            raise _err(exc) from exc
    try:
        sol, built = planning.solve(db, ws, body.policy)
        plan = planning.persist(db, ws, sol, built, ctx.actor, publish_now=body.publish)
    except planning.PlanningError as exc:
        db.rollback()
        raise _err(exc) from exc
    db.commit()
    return {"plan_id": plan.id, "version": plan.version, "status": plan.status, "kpis": plan.kpis, "solver": plan.solver}


@router.post("/plans/{plan_id}/publish")
def publish(plan_id: int, ctx: Ctx = Depends(dispatcher)) -> dict:
    plan = planning.current_plan(ctx.db, ctx.ws)
    if not plan or plan.id != plan_id:
        raise HTTPException(404, "That plan is not the current plan.")
    planning.publish(ctx.db, ctx.ws, plan, ctx.actor)
    ctx.db.commit()
    return {"ok": True, "version": plan.version}


@router.get("/plans/{plan_id}/lever")
def lever(plan_id: int, ctx: Ctx = Depends(dispatcher)) -> dict:
    plan = planning.current_plan(ctx.db, ctx.ws)
    if not plan or plan.id != plan_id:
        raise HTTPException(404, "That plan is not the current plan.")
    out = planning.lever(ctx.db, ctx.ws, plan)
    ctx.db.commit()
    return out


class PolicyIn(BaseModel):
    policy: str = Field(pattern="^(throughput|balanced|fairness)$")


@router.post("/plans/{plan_id}/policy")
def apply_policy(plan_id: int, body: PolicyIn, ctx: Ctx = Depends(dispatcher)) -> dict:
    plan = planning.current_plan(ctx.db, ctx.ws)
    if not plan or plan.id != plan_id:
        raise HTTPException(404, "That plan is not the current plan.")
    try:
        new = planning.apply_policy(ctx.db, ctx.ws, plan, body.policy, ctx.actor)
    except planning.PlanningError as exc:
        ctx.db.rollback()
        raise _err(exc) from exc
    ctx.db.commit()
    return {"plan_id": new.id, "version": new.version, "status": new.status, "kpis": new.kpis}


class MoveIn(BaseModel):
    order_id: int
    vehicle_id: str
    trip_no: int = Field(ge=1, le=2)


@router.post("/moves")
def move(body: MoveIn, ctx: Ctx = Depends(dispatcher)) -> dict:
    try:
        out = planning.move_order(ctx.db, ctx.ws, body.order_id, body.vehicle_id, body.trip_no, ctx.actor)
    except planning.PlanningError as exc:
        ctx.db.rollback()
        raise _err(exc) from exc
    if out.get("ok"):
        ctx.db.commit()
    else:
        ctx.db.rollback()
    return out


@router.get("/moves/options")
def move_options(order_id: int, ctx: Ctx = Depends(dispatcher)) -> list[dict]:
    return planning.move_options(ctx.db, ctx.ws, order_id)


class DeferIn(BaseModel):
    reason: str = Field(min_length=2, max_length=60)


@router.post("/orders/{order_id}/defer")
def defer(order_id: int, body: DeferIn, ctx: Ctx = Depends(dispatcher)) -> dict:
    try:
        out = planning.defer_order(ctx.db, ctx.ws, order_id, body.reason, ctx.actor)
    except planning.PlanningError as exc:
        ctx.db.rollback()
        raise _err(exc) from exc
    ctx.db.commit()
    return out


@router.post("/orders/{order_id}/split-request")
def split_request(order_id: int, ctx: Ctx = Depends(dispatcher)) -> dict:
    """An order larger than any vehicle can never be planned: ask the store to split it."""
    o = ctx.db.get(Order, order_id)
    if not o or o.workspace_id != ctx.ws.id:
        raise HTTPException(404, "Unknown order")
    largest = max(v.volume_cap_m3 for v in planning.RefCache.load(ctx.db).vehicles.values())
    parts = max(2, int(o.volume_m3 // (largest * 0.9)) + 1)
    name = planning.RefCache.outlets[o.outlet_id].name
    post_notice(
        ctx.db,
        ctx.ws,
        f"outlet:{o.outlet_id}",
        "Please split your order",
        f"{o.ref} is {o.volume_m3:.1f} m³, larger than our biggest vehicle ({largest:.0f} m³). "
        f"Send it as {parts} orders of about {o.volume_m3 / parts:.0f} m³ each and they go on the next run.",
        kind="amber",
        icon="split",
        key=f"split:{o.id}",
    )
    post_notice(
        ctx.db,
        ctx.ws,
        "dispatch",
        f"Split requested · {name}",
        f"{parts} orders of about {o.volume_m3 / parts:.0f} m³ each.",
        kind="blue",
        icon="split",
    )
    record(
        ctx.db,
        ctx.ws,
        "order.split_requested",
        actor_name=ctx.actor,
        actor_role="dispatcher",
        entity=f"order:{o.id}",
        topics=[f"outlet:{o.outlet_id}"],
    )
    ctx.db.commit()
    return {"ok": True, "message": f"Asked {name} to send {parts} smaller orders."}


@router.post("/trips/{trip_id}/lock")
def lock(trip_id: int, ctx: Ctx = Depends(dispatcher)) -> dict:
    t = ctx.db.get(Trip, trip_id)
    if not t or t.workspace_id != ctx.ws.id:
        raise HTTPException(404, "Unknown trip")
    t.locked = not t.locked
    record(
        ctx.db, ctx.ws, "trip.locked" if t.locked else "trip.unlocked", actor_name=ctx.actor, actor_role="dispatcher", entity=f"trip:{t.id}"
    )
    ctx.db.commit()
    return {"ok": True, "locked": t.locked}


@router.get("/issues/{issue_id}/options")
def issue_options(issue_id: int, ctx: Ctx = Depends(dispatcher)) -> dict:
    i = ctx.db.get(Issue, issue_id)
    if not i or i.workspace_id != ctx.ws.id:
        raise HTTPException(404, "Unknown issue")
    return {
        "options": fieldops.shortfall_options(ctx.db, ctx.ws, i)
        if i.kind.endswith("dock")
        else [{"key": "ack", "recommended": True, "title": "Acknowledge", "body": "Mark it handled."}]
    }


class DecideIn(BaseModel):
    decision: str = Field(pattern="^(partial|hold|next|ack)$")


@router.post("/issues/{issue_id}/decide")
def decide(issue_id: int, body: DecideIn, ctx: Ctx = Depends(dispatcher)) -> dict:
    try:
        out = fieldops.decide_issue(ctx.db, ctx.ws, ctx.actor, issue_id, body.decision)
    except fieldops.FieldError as exc:
        raise _err(exc) from exc
    ctx.db.commit()
    return out


@router.post("/feed/{notice_id}/dismiss")
def dismiss(notice_id: int, ctx: Ctx = Depends(dispatcher)) -> dict:
    n = ctx.db.get(Notice, notice_id)
    if n and n.workspace_id == ctx.ws.id:
        n.resolved = True
        if n.key:
            resolve_notices(ctx.db, ctx.ws, n.audience, n.key)
        ctx.db.commit()
    return {"ok": True}


@router.get("/outlook")
def outlook(ctx: Ctx = Depends(dispatcher)) -> dict:
    """Refrigerated-vehicle days needed (from the weekly demand forecast) against what the fleet
    can supply once workshop time is taken out, for the next 10 weeks."""
    path = Path(get_settings().data_dir) / "demo" / "forecast_weekly.json"
    if not path.exists():
        raise HTTPException(404, "No forecast file. Run `make forecast`.")
    fc = json.loads(path.read_text())
    per_day = fc.get("reefer_m3_per_vehicle_day", {"Kandy": 6.2, "Peliyagoda": 7.7})
    ref = planning.RefCache.load(ctx.db)
    reefers = {d: [v for v in ref.vehicles.values() if v.depot == d and v.temp == "reefer"] for d in ("Peliyagoda", "Kandy")}
    vdays = {vd.vehicle_id: vd for vd in ctx.db.query(VehicleDay).filter_by(workspace_id=ctx.ws.id, date=ctx.ws.service_date)}
    weeks = []
    for w in fc["weeks"]:
        start = date.fromisoformat(w["week_start"])
        ops = w["operating_days"]
        need = have = 0.0
        by_depot = {}
        for depot in ("Peliyagoda", "Kandy"):
            chilled = sum(g["chilled_m3"] for g in w["groups"].values() if g["depot"] == depot)
            n = chilled / per_day.get(depot, 7.0)
            avail = 0
            for v in reefers[depot]:
                vd = vdays.get(v.id)
                back = vd.back_on if vd and vd.status == "in_workshop" else None
                if back is None or back <= start:
                    avail += ops
                elif back <= start + timedelta(days=6):
                    avail += max(0, ops - (back - start).days)
            by_depot[depot] = {"need": round(n), "have": avail, "chilled_m3": round(chilled), "short": max(0, round(n) - avail)}
            need += n
            have += avail
        weeks.append(
            {
                "week_start": w["week_start"],
                "label": f"{start:%-d %b}",
                "need": round(need),
                "have": int(have),
                # refrigerated vehicles serve their own depot's stores, so a surplus at one depot
                # does not cover a gap at the other: the shortfall is counted depot by depot
                "short": sum(d["short"] for d in by_depot.values()),
                "festivals": w["festivals"],
                "operating_days": ops,
                "depots": by_depot,
            }
        )
    worst = max(weeks, key=lambda x: x["short"]) if weeks else None
    return {
        "weeks": weeks,
        "method": fc.get("method"),
        "backtest": fc.get("backtest"),
        "history_end": fc.get("history_end"),
        "per_vehicle_day": per_day,
        "worst": worst,
        "now": now_virtual(ctx.ws).isoformat(),
    }
