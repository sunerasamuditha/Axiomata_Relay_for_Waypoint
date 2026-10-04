"""What happens at the dock, on the road and at the store counter.

Every function here is called both online (a direct request) and offline-replayed (from
/api/sync, with the time the device recorded). The rules:

  * the event time is the device's time; the workspace clock only ever moves forward to meet it
  * a physical fact (a line loaded, a stop delivered) is never undone by a plan change made while
    the device could not hear it: a queued move for a stop that was delivered is cancelled
  * every change notifies the faces that care (dock, driver, store, dispatcher)
"""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import DeviceSeen, Issue, Media, Order, OrderLine, PendingMove, Proof, Receipt, Stop, Trip, User, Workspace
from .clock import advance_to, now_virtual, utcnow
from .eta import RefCache
from .events import notify, post_notice, record, resolve_notices
from .handover import store_manager_for, verify


class FieldError(Exception):
    pass


# Issues a loader raises at the dock (they block release until the dispatcher decides).
DOCK_KINDS = ("short_at_dock", "damaged_at_dock", "wrong_at_dock")
DOCK_KIND_FOR = {"missing": "short_at_dock", "damaged": "damaged_at_dock", "wrong": "wrong_at_dock"}
DOCK_WORD = {"missing": "missing", "damaged": "damaged", "wrong": "wrong item"}


# ------------------------------------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------------------------------------


def save_media(db: Session, ws: Workspace, data_url: str | None) -> str | None:
    if not data_url:
        return None
    try:
        head, b64 = data_url.split(",", 1)
        ctype = head.split(";")[0].split(":")[1] if ":" in head else "image/jpeg"
        raw = base64.b64decode(b64)
    except (ValueError, IndexError, binascii.Error) as exc:
        raise FieldError("The photo could not be read.") from exc
    if len(raw) > get_settings().max_media_bytes:
        raise FieldError("The photo is too large.")
    if ctype not in ("image/jpeg", "image/png", "image/webp", "image/svg+xml"):
        raise FieldError("Unsupported image type.")
    mid = str(uuid.uuid4())
    db.add(Media(id=mid, workspace_id=ws.id, content_type=ctype, size=len(raw), data=raw, created_at=utcnow()))
    return mid


def _when(ws: Workspace, at: datetime | None, floor: datetime | None = None) -> datetime:
    """Event time: what the device says (or now), never before `floor`; the clock catches up."""
    t = at or now_virtual(ws)
    if floor and t < floor:
        t = floor
    advance_to(ws, t)
    return t


def outlet_name(oid: str) -> str:
    return RefCache.outlets[oid].name


def _trip_for_user(db: Session, user: User, trip_id: int) -> Trip:
    t = db.get(Trip, trip_id)
    if not t:
        raise FieldError("Unknown trip.")
    if user.role == "driver" and t.vehicle_id != user.vehicle_id:
        raise FieldError("That trip belongs to another vehicle.")
    if user.role == "loader" and t.depot != user.depot:
        raise FieldError("That trip loads at another depot.")
    return t


# ------------------------------------------------------------------------------------------------
# dock
# ------------------------------------------------------------------------------------------------


def check_line(db: Session, ws: Workspace, user: User, line_id: int, loaded: bool, crew: str | None, at: datetime | None) -> dict:
    line = db.get(OrderLine, line_id)
    if not line:
        raise FieldError("Unknown line.")
    stop = db.query(Stop).filter_by(order_id=line.order_id).first()
    if not stop:
        raise FieldError("This line is not on a trip.")
    trip = _trip_for_user(db, user, stop.trip_id)
    if trip.status in ("loaded", "out", "done"):
        raise FieldError(f"{trip.vehicle_id} has already been released.")
    first = trip.status == "planned"
    when = _when(ws, at, trip.planned_depart - timedelta(minutes=75) if first else None)
    if first:
        trip.status = "loading"
        trip.loading_started_at = when
        trip.controlled = "human"
    if line.load_state in ("short", "damaged") and loaded:
        raise FieldError("This line is flagged. Wait for the dispatcher's decision.")
    line.load_state = "loaded" if loaded else "todo"
    line.loaded_qty = line.qty if loaded else None
    line.loaded_by = crew or user.short_name
    line.loaded_at = when if loaded else None
    record(
        db,
        ws,
        "line.loaded" if loaded else "line.unloaded",
        actor_name=crew or user.short_name,
        actor_role="loader",
        entity=f"line:{line.id}",
        payload={"trip": trip.code, "vehicle": trip.vehicle_id},
        topics=[f"depot:{trip.depot}"],
    )
    if first:
        post_notice(
            db,
            ws,
            "dispatch",
            f"{trip.vehicle_id} loading at {trip.depot}",
            f"{crew or user.short_name} started trip {trip.trip_no} at {when:%H:%M}.",
            kind="blue",
            icon="box",
            key=f"load:{trip.id}",
            actions=[["Locate", f"locate:{trip.code}"]],
        )
    return {"ok": True, "trip_status": trip.status}


def flag_line(
    db: Session,
    ws: Workspace,
    user: User,
    line_id: int,
    kind: str,
    qty: int,
    note: str,
    photo: str | None,
    crew: str | None,
    at: datetime | None,
) -> dict:
    line = db.get(OrderLine, line_id)
    stop = db.query(Stop).filter_by(order_id=line.order_id).first() if line else None
    if not line or not stop:
        raise FieldError("Unknown line.")
    trip = _trip_for_user(db, user, stop.trip_id)
    if kind not in DOCK_KIND_FOR:
        raise FieldError("Flag a line as missing, damaged or the wrong item.")
    qty = max(1, min(int(qty), line.qty))
    when = _when(ws, at)
    if trip.status == "planned":
        trip.status, trip.loading_started_at, trip.controlled = "loading", when, "human"
    line.load_state = "damaged" if kind == "damaged" else "short"
    line.loaded_qty = line.qty - qty
    order = line.order
    issue = Issue(
        workspace_id=ws.id,
        kind=DOCK_KIND_FOR[kind],
        severity="critical",
        trip_id=trip.id,
        stop_id=stop.id,
        order_id=order.id,
        line_id=line.id,
        qty=qty,
        note=note or "",
        raised_by=crew or user.short_name,
        raised_role="loader",
        raised_at=when,
        photo_media_id=save_media(db, ws, photo),
    )
    db.add(issue)
    db.flush()
    name = outlet_name(order.outlet_id)
    record(
        db,
        ws,
        "line.flagged",
        actor_name=crew or user.short_name,
        actor_role="loader",
        entity=f"line:{line.id}",
        payload={"kind": kind, "qty": qty, "trip": trip.code, "issue": issue.id},
        topics=[f"depot:{trip.depot}", f"vehicle:{trip.vehicle_id}"],
    )
    post_notice(
        db,
        ws,
        "dispatch",
        f"Short at the dock · {trip.vehicle_id}",
        f"{crew or user.short_name} flagged {qty} × {line.name} {DOCK_WORD[kind]} for {name} (stop {stop.seq + 1}). {trip.vehicle_id} departs {trip.planned_depart:%H:%M}.",
        kind="ember",
        icon="box",
        key=f"issue:{issue.id}",
        entity=f"issue:{issue.id}",
        actions=[["Decide", f"shortfall:{issue.id}"], ["Locate", f"locate:{trip.code}"]],
    )
    return {"ok": True, "issue_id": issue.id}


def shortfall_options(db: Session, ws: Workspace, issue: Issue) -> list[dict]:
    trip = db.get(Trip, issue.trip_id)
    line = db.get(OrderLine, issue.line_id)
    stops = sorted(trip.stops, key=lambda s: s.seq)
    later = [s for s in stops if s.seq > (db.get(Stop, issue.stop_id).seq if issue.stop_id else 0)]
    hold_risk = max((min(0.95, s.late_prob + 0.25) for s in later), default=0.3)
    return [
        {
            "key": "partial",
            "recommended": True,
            "title": "Send a partial delivery and tell the store",
            "body": f"Leaves on time. {issue.qty} × {line.name} follow on the next run. No stop misses its window.",
        },
        {
            "key": "hold",
            "recommended": False,
            "title": f"Hold {trip.vehicle_id} for 20 minutes",
            "body": f"Picking can find the stock, but the later stops rise to about {round(hold_risk * 100)}% late risk.",
        },
        {
            "key": "next",
            "recommended": False,
            "title": "Remove the line and re-order for tomorrow",
            "body": "The store gets a fresh order for the missing quantity on tomorrow's plan.",
        },
    ]


def decide_issue(db: Session, ws: Workspace, actor: str, issue_id: int, decision: str) -> dict:
    issue = db.get(Issue, issue_id)
    if not issue or issue.workspace_id != ws.id:
        raise FieldError("Unknown issue.")
    if issue.status != "open":
        raise FieldError("Already decided.")
    trip = db.get(Trip, issue.trip_id) if issue.trip_id else None
    line = db.get(OrderLine, issue.line_id) if issue.line_id else None
    order = db.get(Order, issue.order_id) if issue.order_id else None
    now = now_virtual(ws)
    text = {
        "partial": f"Send partial; {issue.qty} × {line.name if line else 'items'} follow on the next run",
        "hold": f"Hold {trip.vehicle_id if trip else 'the vehicle'} for 20 minutes",
        "next": "Remove the line; re-order for tomorrow",
        "ack": "Acknowledged",
    }.get(decision)
    if not text:
        raise FieldError("Unknown decision.")
    issue.status, issue.decision, issue.decision_text, issue.decided_by, issue.decided_at = "decided", decision, text, actor, now
    if decision == "hold" and trip and trip.status in ("planned", "loading", "loaded"):
        trip.planned_depart += timedelta(minutes=20)
        for s in trip.stops:
            s.planned_arrival += timedelta(minutes=20)
            s.planned_start += timedelta(minutes=20)
            s.planned_finish += timedelta(minutes=20)
            s.late_prob = min(0.95, s.late_prob + 0.2)
        if line:
            line.load_state, line.loaded_qty = "todo", None
    if decision in ("partial", "next") and line and issue.kind in DOCK_KINDS:
        line.load_state = "loaded"  # what is there goes; the shortfall is known and recorded
    if decision == "next" and order and line:
        from .ordering import place_order

        place_order(
            db,
            ws,
            order.outlet_id,
            order.temp,
            [(line.sku, issue.qty or 1)],
            channel="phone",
            at=now,
            service_date=ws.service_date + timedelta(days=1),
            note=f"Follow-up for {issue.qty} × {line.name} short on {order.ref}",
        )
    resolve_notices(db, ws, "dispatch", f"issue:{issue.id}")
    name = outlet_name(order.outlet_id) if order else ""
    topics = [f"depot:{trip.depot}", f"vehicle:{trip.vehicle_id}"] if trip else []
    if order:
        topics.append(f"outlet:{order.outlet_id}")
    record(
        db,
        ws,
        "issue.decided",
        actor_name=actor,
        actor_role="dispatcher",
        entity=f"issue:{issue.id}",
        payload={"decision": decision},
        topics=topics,
    )
    post_notice(
        db,
        ws,
        "dispatch",
        "Shortfall decided" if issue.kind.endswith("dock") else "Issue handled",
        f"{text}. The dock, the driver and {name or 'the store'} have been told.",
        kind="green",
        icon="check",
    )
    if trip:
        post_notice(
            db,
            ws,
            f"depot:{trip.depot}",
            f"Decision from {actor}: {text}",
            f"{trip.vehicle_id} · {name}. Keep loading; release unlocks now.",
            kind="green",
            icon="check",
            key=f"issue:{issue.id}",
        )
        post_notice(
            db,
            ws,
            f"vehicle:{trip.vehicle_id}",
            f"Known shortfall at {name}" if decision != "hold" else "Departure moved 20 minutes",
            f"{issue.qty} × {line.name if line else ''} {'will not be on board' if decision != 'hold' else 'are being picked'}. {text}.",
            kind="amber",
            icon="box",
            key=f"issue:{issue.id}",
        )
    if order and issue.kind.endswith("dock") and decision in ("partial", "next"):
        post_notice(
            db,
            ws,
            f"outlet:{order.outlet_id}",
            f"{issue.qty} × {line.name if line else 'items'} short on today's delivery",
            "The rest arrives as planned. The missing cases follow on the next run.",
            kind="amber",
            icon="box",
            key=f"short:{issue.id}",
        )
    return {"ok": True, "decision": decision, "text": text}


def release_trip(
    db: Session,
    ws: Workspace,
    user: User,
    trip_id: int,
    temp_c: float | None,
    seal: str,
    doors_ok: bool,
    photo: str | None,
    crew: str | None,
    at: datetime | None,
) -> dict:
    trip = _trip_for_user(db, user, trip_id)
    if trip.status in ("loaded", "out", "done"):
        return {"ok": True, "already": True}
    open_issue = db.query(Issue).filter_by(trip_id=trip.id, status="open").filter(Issue.kind.in_(DOCK_KINDS)).first()
    if open_issue:
        raise FieldError("A shortfall is waiting for the dispatcher. Release unlocks when she decides.")
    todo = [ln for s in trip.stops for ln in s.order.lines if ln.load_state == "todo"]
    if todo:
        raise FieldError(f"{len(todo)} line{'s' if len(todo) != 1 else ''} still to load.")
    v = RefCache.vehicles[trip.vehicle_id]
    needs_temp = v.temp == "reefer" and any(s.order.temp == "chilled" for s in trip.stops)
    if needs_temp and (temp_c is None or not (-25 <= float(temp_c) <= 5)):
        raise FieldError("Record the reefer temperature (it must be 5 °C or colder for chilled goods).")
    if not seal or len(seal) < 4:
        raise FieldError("Enter the seal number.")
    when = _when(ws, at, trip.planned_depart - timedelta(minutes=10))
    trip.status, trip.released_at, trip.released_by = "loaded", when, crew or user.short_name
    trip.release_temp_c, trip.seal_no, trip.doors_ok = temp_c, seal, doors_ok
    if photo:
        save_media(db, ws, photo)
    for s in trip.stops:
        s.order.status = "loaded"
    shorts = db.query(Issue).filter_by(trip_id=trip.id).filter(Issue.kind.in_(DOCK_KINDS)).count()
    record(
        db,
        ws,
        "trip.released",
        actor_name=crew or user.short_name,
        actor_role="loader",
        entity=f"trip:{trip.id}",
        payload={"temp_c": temp_c, "seal": seal},
        topics=[f"depot:{trip.depot}", f"vehicle:{trip.vehicle_id}", "plan"] + [f"outlet:{s.order.outlet_id}" for s in trip.stops],
    )
    post_notice(
        db,
        ws,
        "dispatch",
        f"{trip.vehicle_id} released at {trip.depot}",
        f"{crew or user.short_name} released it at {when:%H:%M}"
        + (f", {temp_c:g} °C" if temp_c is not None else "")
        + f", seal {seal}. "
        + (f"{shorts} shortfall{'s' if shorts != 1 else ''} on board, decided." if shorts else "Nothing missing."),
        kind="green",
        icon="box",
        key=f"load:{trip.id}",
        actions=[["Locate", f"locate:{trip.code}"]],
    )
    post_notice(
        db,
        ws,
        f"vehicle:{trip.vehicle_id}",
        "Your run is loaded and sealed",
        f"Seal {seal}"
        + (f" · {temp_c:g} °C" if temp_c is not None else "")
        + f". Departure {trip.planned_depart:%H:%M}. Start the run when you leave.",
        kind="green",
        icon="check",
        key="released",
    )
    return {"ok": True}


# ------------------------------------------------------------------------------------------------
# road
# ------------------------------------------------------------------------------------------------


def start_run(db: Session, ws: Workspace, user: User, trip_id: int, at: datetime | None) -> dict:
    trip = _trip_for_user(db, user, trip_id)
    if trip.status in ("out", "done"):
        return {"ok": True, "already": True}
    if trip.status != "loaded":
        raise FieldError("The dock has not released this vehicle yet.")
    when = _when(ws, at, trip.planned_depart)
    trip.status, trip.departed_at, trip.last_contact_at = "out", when, when
    for s in trip.stops:
        s.order.status = "out"
    names = ", ".join(sorted({outlet_name(s.order.outlet_id) for s in trip.stops}))
    record(
        db,
        ws,
        "run.started",
        actor_name=user.short_name,
        actor_role="driver",
        entity=f"trip:{trip.id}",
        topics=[f"vehicle:{trip.vehicle_id}", "plan"] + [f"outlet:{s.order.outlet_id}" for s in trip.stops],
        device_at=at,
    )
    post_notice(
        db,
        ws,
        "dispatch",
        f"{trip.vehicle_id} left {trip.depot}",
        f"{user.short_name} started the {trip.district} run at {when:%H:%M}. {len(trip.stops)} stops.",
        kind="green",
        icon="truck",
        key=f"run:{trip.id}",
        actions=[["Locate", f"locate:{trip.code}"]],
    )
    for oid in {s.order.outlet_id for s in trip.stops}:
        seq = min(s.seq for s in trip.stops if s.order.outlet_id == oid) + 1
        post_notice(
            db,
            ws,
            f"outlet:{oid}",
            "Your delivery is on the way",
            f"{trip.vehicle_id} left {trip.depot} at {when:%H:%M}. You are stop {seq} of {len({s.order.outlet_id for s in trip.stops})}.",
            kind="blue",
            icon="truck",
            key=f"run:{trip.id}",
        )
    _ = names
    return {"ok": True}


def _stops_for(db: Session, user: User, stop_ids: list[int]) -> list[Stop]:
    stops = [db.get(Stop, i) for i in stop_ids]
    if not stops or any(s is None for s in stops):
        raise FieldError("Unknown stop.")
    trip = stops[0].trip
    if user.role == "driver" and trip.vehicle_id != user.vehicle_id:
        cancelled = [s for s in stops if s.trip.vehicle_id != user.vehicle_id]
        if cancelled:
            raise FieldError("moved")
    return stops


def arrive(db: Session, ws: Workspace, user: User, stop_ids: list[int], at: datetime | None, offline: bool = False) -> dict:
    stops = _stops_for(db, user, stop_ids)
    trip = stops[0].trip
    if trip.status == "loaded":
        start_run(db, ws, user, trip.id, at)
    when = _when(ws, at, min(s.planned_arrival for s in stops) if not at else None)
    for s in stops:
        if s.status == "pending":
            s.status, s.arrived_at = "arrived", when
            s.recorded_offline = s.recorded_offline or offline
    trip.last_contact_at = when
    oid = stops[0].order.outlet_id
    record(
        db,
        ws,
        "stop.arrived",
        actor_name=user.short_name,
        actor_role="driver",
        entity=f"stop:{stops[0].id}",
        device_at=at,
        topics=[f"vehicle:{trip.vehicle_id}", f"outlet:{oid}"],
        payload={"offline": offline},
    )
    post_notice(
        db,
        ws,
        f"outlet:{oid}",
        f"{trip.vehicle_id} is at your door",
        f"{user.short_name} arrived at {when:%H:%M}.",
        kind="blue",
        icon="pin",
        key=f"run:{trip.id}",
    )
    return {"ok": True}


def complete(
    db: Session,
    ws: Workspace,
    user: User,
    stop_ids: list[int],
    outcome: str,
    lines: list[dict],
    receiver: str,
    photo: str | None,
    signature: str | None,
    note: str,
    at: datetime | None,
    offline: bool = False,
    client_event_id: str | None = None,
    *,
    visit_id: int | None = None,
    pin_proof: str | None = None,
) -> dict:
    """Record a delivery. If a dispatcher queued a move for one of these stops while the phone was
    dark, the delivery wins and the move is cancelled.

    Where the store manager has a delivery PIN, the phone sends `pin_proof` once they typed it
    (domain/handover.py). A missing or wrong proof never blocks the delivery (physical facts win): it
    is recorded unconfirmed and the store and dispatch are told."""
    if outcome not in ("delivered", "partial", "failed"):
        raise FieldError("Unknown outcome.")
    stops = _stops_for(db, user, stop_ids)
    trip = stops[0].trip
    if all(s.status in ("delivered", "partial", "failed") for s in stops):
        return {"ok": True, "conflicts": [], "already": True}  # a repeat (two taps, or a resend): nothing new to tell anyone
    if trip.status == "loaded":
        start_run(db, ws, user, trip.id, at)
    floor = None if at else max((s.arrived_at or s.planned_start) for s in stops) + timedelta(minutes=8)
    when = _when(ws, at, floor)
    pmid = save_media(db, ws, photo)
    smid = save_media(db, ws, signature)
    # a refused or closed stop has no handover, so no PIN is expected
    manager = store_manager_for(db, stops[0].order.outlet_id) if outcome != "failed" else None
    try:
        vid = int(visit_id) if visit_id is not None else None
    except (TypeError, ValueError):
        vid = None
    pin_ok = bool(manager and manager.delivery_pin and vid in stop_ids and verify(vid, manager.delivery_pin, pin_proof))
    received_by = ((manager.name if manager and pin_ok else receiver) or "")[:60]
    qty = {int(x["line_id"]): int(x.get("delivered_qty", 0)) for x in lines or []}
    conflicts = []
    for s in stops:
        if s.status in ("delivered", "partial", "failed"):
            continue
        for ln in s.order.lines:
            ln.delivered_qty = qty.get(ln.id, ln.loaded_qty if ln.loaded_qty is not None else ln.qty) if outcome != "failed" else 0
        # one visit can carry several orders (chilled and dry for the same store): each order is
        # complete or partial on its own lines
        own = outcome
        if outcome in ("delivered", "partial"):
            own = "partial" if any((ln.delivered_qty or 0) < ln.qty for ln in s.order.lines) else "delivered"
        s.status = own
        s.arrived_at = s.arrived_at or when - timedelta(minutes=max(5, round(s.pred_service_min or 15)))
        s.completed_at = when
        s.synced_at = utcnow()
        s.recorded_offline = s.recorded_offline or offline
        s.outcome_note = note or ""
        s.order.status = own
        db.add(
            Proof(
                workspace_id=ws.id,
                stop_id=s.id,
                receiver_name=received_by,
                photo_media_id=pmid,
                signature_media_id=smid,
                note=note or "",
                captured_at=when,
                pin_verified=pin_ok,
            )
        )
        for pm in db.query(PendingMove).filter_by(order_id=s.order_id, status="queued"):
            pm.status, pm.resolved_at = "cancelled", now_virtual(ws)
            pm.resolution = f"{user.short_name} delivered at {when:%H:%M} while offline, so the move to {pm.to_vehicle_id} was cancelled."
            conflicts.append(pm)
    trip.last_contact_at = max(trip.last_contact_at or when, when)
    if outcome != "failed":
        outcome = "partial" if any(s.status == "partial" for s in stops) else "delivered"
    oid = stops[0].order.outlet_id
    name = outlet_name(oid)
    if all(s.status != "pending" and s.status != "arrived" for s in trip.stops):
        trip.status, trip.completed_at = "done", when
    record(
        db,
        ws,
        "stop.completed",
        actor_name=user.short_name,
        actor_role="driver",
        entity=f"stop:{stops[0].id}",
        device_at=at,
        client_event_id=None,
        payload={"outcome": outcome, "offline": offline, "orders": [s.order.ref for s in stops]},
        topics=[f"vehicle:{trip.vehicle_id}", f"outlet:{oid}", "plan"],
    )
    late = any(s.completed_at and s.arrived_at and s.arrived_at > _close(s) for s in stops)
    delivered = f"{user.short_name} delivered at {when:%H:%M}" + (" while out of coverage" if offline else "")
    if pin_ok:
        store_body = f"{delivered}. Please confirm what arrived. Confirmed with your PIN."
    elif manager:
        store_body = f"{delivered}. It was recorded without your PIN, so please confirm what arrived."
    else:
        store_body = f"{delivered}. Please confirm what arrived."
    post_notice(
        db,
        ws,
        f"outlet:{oid}",
        "Delivered" if outcome == "delivered" else ("Delivered, partial" if outcome == "partial" else "Delivery failed"),
        store_body,
        kind="green" if outcome == "delivered" else "amber",
        icon="check",
        key=f"run:{trip.id}",
        entity=f"order:{stops[0].order_id}",
    )
    if outcome != "delivered" or late:
        post_notice(
            db,
            ws,
            "dispatch",
            f"{name} · {outcome}" + (" · late" if late else ""),
            note or f"{trip.vehicle_id} recorded it at {when:%H:%M}.",
            kind="amber",
            icon="alert",
            actions=[["Locate", f"locate:{trip.code}"]],
        )
    if manager and not pin_ok:
        post_notice(
            db,
            ws,
            "dispatch",
            f"{name} · delivered without the store's PIN",
            f"{user.short_name} recorded it at {when:%H:%M}"
            + (" while out of coverage" if offline else "")
            + f" without {manager.short_name}'s PIN"
            + (f" (received by {received_by})" if received_by else "")
            + f". {manager.short_name} has been asked to confirm what arrived.",
            kind="amber",
            icon="alert",
            actions=[["Locate", f"locate:{trip.code}"]],
        )
    for pm in conflicts:
        target = pm.to_vehicle_id
        post_notice(
            db,
            ws,
            "dispatch",
            "Conflict resolved: delivery kept",
            pm.resolution + f" {target} has been told to skip it.",
            kind="ember",
            icon="alert",
            key=f"move:{pm.order_id}",
            actions=[["Show trip", f"locate:{trip.code}"]],
        )
        post_notice(
            db,
            ws,
            f"vehicle:{target}",
            f"Skip {name}",
            f"{user.short_name} already delivered it. It is off your run.",
            kind="amber",
            icon="x",
            key=f"move:{pm.order_id}",
        )
        notify(db, ws.id, [f"vehicle:{target}"])
    return {"ok": True, "conflicts": [pm.resolution for pm in conflicts]}


def _close(s: Stop) -> datetime:
    ou = RefCache.outlets[s.order.outlet_id]
    h, m = ou.window_close.split(":")
    return datetime.combine(s.trip.service_date, datetime.min.time()) + timedelta(hours=int(h), minutes=int(m))


def report_problem(
    db: Session,
    ws: Workspace,
    user: User,
    trip_id: int,
    stop_id: int | None,
    kind: str,
    note: str,
    photo: str | None,
    at: datetime | None,
    offline: bool = False,
) -> dict:
    trip = _trip_for_user(db, user, trip_id)
    when = _when(ws, at)
    labels = {
        "traffic": "Traffic or road closed",
        "vehicle": "Vehicle problem",
        "store_closed": "Store closed or no one to receive",
        "damaged": "Damaged goods",
        "temperature": "Reefer temperature alarm",
        "late": "Running late",
        "refused": "Goods refused",
        "other": "Other problem",
    }
    issue = Issue(
        workspace_id=ws.id,
        kind="driver_report",
        severity="warn",
        trip_id=trip.id,
        stop_id=stop_id,
        note=note or "",
        raised_by=user.short_name,
        raised_role="driver",
        raised_at=when,
        photo_media_id=save_media(db, ws, photo),
        payload={"kind": kind, "offline": offline},
    )
    db.add(issue)
    db.flush()
    record(
        db,
        ws,
        "driver.report",
        actor_name=user.short_name,
        actor_role="driver",
        entity=f"issue:{issue.id}",
        device_at=at,
        payload={"kind": kind},
        topics=[f"vehicle:{trip.vehicle_id}"],
    )
    post_notice(
        db,
        ws,
        "dispatch",
        f"Driver report · {trip.vehicle_id}",
        f"{labels.get(kind, kind)}{': ' + note if note else ''}. Recorded {when:%H:%M}" + (" offline." if offline else "."),
        kind="ember",
        icon="flag",
        key=f"issue:{issue.id}",
        actions=[["Acknowledge", f"ackissue:{issue.id}"], ["Locate", f"locate:{trip.code}"]],
    )
    return {"ok": True, "issue_id": issue.id}


# ------------------------------------------------------------------------------------------------
# connectivity (Dark Corridor)
# ------------------------------------------------------------------------------------------------


def heartbeat(db: Session, ws: Workspace, user: User, pending: int = 0) -> dict:
    ds = db.get(DeviceSeen, (ws.id, user.id))
    now_real = utcnow()
    if not ds:
        ds = DeviceSeen(workspace_id=ws.id, user_id=user.id, last_seen_real=now_real)
        db.add(ds)
    prev = ds.last_seen_real if ds.last_seen_real.tzinfo else ds.last_seen_real.replace(tzinfo=now_real.tzinfo)
    away_s = max(0.0, (now_real - prev).total_seconds())
    ds.last_seen_real, ds.last_virtual, ds.pending = now_real, now_virtual(ws), pending
    changes = []
    if user.role == "driver" and user.vehicle_id:
        for trip in db.query(Trip).filter_by(workspace_id=ws.id, vehicle_id=user.vehicle_id, service_date=ws.service_date):
            if trip.signal == "dark":
                since = trip.dark_since
                trip.signal, trip.dark_since = "ok", None
                trip.last_contact_at = now_virtual(ws)
                resolve_notices(db, ws, "dispatch", f"dark:{trip.id}")
                post_notice(
                    db,
                    ws,
                    "dispatch",
                    f"{trip.vehicle_id} back online" + (f" · {pending} record{'s' if pending != 1 else ''} syncing" if pending else ""),
                    f"Dark for {_dark_for(since, now_virtual(ws), away_s)}. Each record keeps the time it happened, not the time it arrived.",
                    kind="green",
                    icon="wifi",
                    key=f"online:{trip.id}",
                    actions=[["Locate", f"locate:{trip.code}"]],
                )
                for oid in {s.order.outlet_id for s in trip.stops}:
                    notify(db, ws.id, [f"outlet:{oid}"])
                notify(db, ws.id, [f"vehicle:{trip.vehicle_id}"])
            # moves queued while the phone was dark wait until its own records are in:
            # a stop it delivered offline must be able to cancel the move
            if pending == 0:
                changes += apply_pending_moves(db, ws, trip)
    return {"ok": True, "changes": changes}


def _dark_for(since: datetime | None, now: datetime, away_s: float) -> str:
    """How long the phone was out of reach: on the demo clock if it moved, else in real time
    (with the clock paused, the van can be dark for a minute of real time at one virtual instant)."""
    if since and (now - since).total_seconds() >= 60:
        return _dur(since, now)
    return f"{round(away_s)} s" if away_s < 90 else f"{round(away_s / 60)} min"


def _dur(a: datetime | None, b: datetime) -> str:
    if not a:
        return "a while"
    m = max(0, int((b - a).total_seconds() // 60))
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m} min"


def detect_dark(db: Session, ws: Workspace) -> bool:
    """Called by the live ticker: a human-driven vehicle on the road that has not been heard from
    for `heartbeat_dark_s` real seconds is marked dark; its stores switch to estimated arrivals."""
    s = get_settings()
    changed = False
    now_real = utcnow()
    drivers = {u.vehicle_id: u for u in db.query(User).filter(User.role == "driver", User.vehicle_id.isnot(None))}
    for trip in db.query(Trip).filter_by(workspace_id=ws.id, service_date=ws.service_date, status="out", controlled="human"):
        u = drivers.get(trip.vehicle_id)
        if not u:
            continue
        ds = db.get(DeviceSeen, (ws.id, u.id))
        if not ds:
            continue
        seen = ds.last_seen_real if ds.last_seen_real.tzinfo else ds.last_seen_real.replace(tzinfo=now_real.tzinfo)
        if trip.signal != "dark" and (now_real - seen).total_seconds() > s.heartbeat_dark_s:
            trip.signal = "dark"
            # the last thing heard from the phone: its last heartbeat or its last record
            heard = [t for t in (ds.last_virtual, trip.last_contact_at) if t]
            trip.dark_since = max(heard) if heard else now_virtual(ws)
            last = max((st for st in trip.stops if st.completed_at), key=lambda st: st.completed_at, default=None)
            post_notice(
                db,
                ws,
                "dispatch",
                f"{trip.vehicle_id} has no signal",
                f"Last contact {trip.dark_since:%H:%M}"
                + (f" after {outlet_name(last.order.outlet_id)}" if last else "")
                + ". Its stores now see estimated arrivals. Anything you change on this van waits for its phone.",
                kind="ember",
                icon="wifiOff",
                key=f"dark:{trip.id}",
                actions=[["Locate", f"locate:{trip.code}"]],
            )
            for oid in {st.order.outlet_id for st in trip.stops}:
                notify(db, ws.id, [f"outlet:{oid}"])
            changed = True
    return changed


def apply_pending_moves(db: Session, ws: Workspace, trip: Trip) -> list[str]:
    """The phone is back: apply moves queued while it was dark, unless the stop was delivered."""
    from .planning import _apply_move, check_target, current_plan

    out = []
    plan = current_plan(db, ws)
    for pm in db.query(PendingMove).filter_by(from_trip_id=trip.id, status="queued").all():
        stop = db.query(Stop).filter_by(order_id=pm.order_id, trip_id=trip.id).first()
        name = outlet_name(db.get(Order, pm.order_id).outlet_id)
        if stop and stop.status in ("delivered", "partial", "failed"):
            pm.status, pm.resolved_at = "cancelled", now_virtual(ws)
            pm.resolution = f"{name} was delivered at {stop.completed_at:%H:%M} while offline; the move was cancelled."
            post_notice(
                db, ws, "dispatch", "Conflict resolved: delivery kept", pm.resolution, kind="ember", icon="alert", key=f"move:{pm.order_id}"
            )
            post_notice(
                db,
                ws,
                f"vehicle:{pm.to_vehicle_id}",
                f"Skip {name}",
                "It was delivered by the original van. It is off your run.",
                kind="amber",
                icon="x",
                key=f"move:{pm.order_id}",
            )
            continue
        order = db.get(Order, pm.order_id)
        res, target, msg = check_target(db, ws, plan, order, pm.to_vehicle_id, pm.to_trip_no)
        if not res or not res.ok:
            pm.status, pm.resolved_at, pm.resolution = "cancelled", now_virtual(ws), f"Could not apply: {msg}"
            # the plan changed while the van was dark: say so instead of leaving "Move queued" up
            post_notice(
                db,
                ws,
                "dispatch",
                f"Move not applied: {name}",
                f"{msg} {name} stays on {trip.vehicle_id}.",
                kind="ember",
                icon="alert",
                key=f"move:{pm.order_id}",
                actions=[["Show trip", f"locate:{trip.code}"]],
            )
            resolve_notices(db, ws, f"vehicle:{pm.to_vehicle_id}", f"move:{pm.order_id}")
            continue
        _apply_move(db, ws, plan, order, stop, target, pm.to_vehicle_id, pm.to_trip_no, pm.created_by, msg)
        pm.status, pm.resolved_at = "applied", now_virtual(ws)
        pm.resolution = f"{trip.vehicle_id} had not reached {name}, so the move to {pm.to_vehicle_id} went through."
        post_notice(
            db,
            ws,
            "dispatch",
            f"Move applied: {name} on {pm.to_vehicle_id}",
            pm.resolution,
            kind="blue",
            icon="check",
            key=f"move:{pm.order_id}",
        )
        post_notice(
            db,
            ws,
            f"vehicle:{trip.vehicle_id}",
            f"{name} moved to {pm.to_vehicle_id}",
            "The dispatcher moved it while you were out of coverage. It is off your run.",
            kind="amber",
            icon="arrowR",
            key=f"move:{pm.order_id}",
        )
        out.append(pm.resolution)
    return out


# ------------------------------------------------------------------------------------------------
# store counter
# ------------------------------------------------------------------------------------------------


def confirm_receipt(db: Session, ws: Workspace, user: User, order_id: int, lines: list[dict], note: str) -> dict:
    order = db.get(Order, order_id)
    if not order or order.workspace_id != ws.id or order.outlet_id != user.outlet_id:
        raise FieldError("Unknown order.")
    if order.status not in ("delivered", "partial", "failed"):
        raise FieldError("You can confirm receipt once the delivery has been recorded.")
    if db.query(Receipt).filter_by(order_id=order.id).first():
        raise FieldError("Already confirmed.")
    by_id = {ln.id: ln for ln in order.lines}
    issues = []
    for x in lines or []:
        ln = by_id.get(int(x["line_id"]))
        if not ln:
            continue
        ln.received_qty = int(x.get("received_qty", ln.delivered_qty or 0))
        ln.receipt_issue = x.get("issue") or None
        ln.receipt_note = (x.get("note") or "")[:200] or None
        if ln.receipt_issue:
            issues.append(ln)
    now = now_virtual(ws)
    db.add(
        Receipt(
            workspace_id=ws.id,
            order_id=order.id,
            by_name=user.name,
            at=now,
            status="confirmed_with_issues" if issues else "confirmed",
            note=note or "",
        )
    )
    order.status = "received"
    stop = db.query(Stop).filter_by(order_id=order.id).first()
    trip = stop.trip if stop else None
    if issues:
        txt = "; ".join(f"{(ln.delivered_qty or ln.qty) - (ln.received_qty or 0) or 1} × {ln.name} {ln.receipt_issue}" for ln in issues)
        issue = Issue(
            workspace_id=ws.id,
            kind="receipt_issue",
            severity="warn",
            trip_id=trip.id if trip else None,
            stop_id=stop.id if stop else None,
            order_id=order.id,
            note=f"{txt}. {note}".strip(),
            raised_by=user.short_name,
            raised_role="store",
            raised_at=now,
        )
        db.add(issue)
        db.flush()
        post_notice(
            db,
            ws,
            "dispatch",
            f"Receipt issue · {outlet_name(order.outlet_id)}",
            f"{user.short_name} confirmed {order.ref} with issues: {txt}.",
            kind="ember",
            icon="flag",
            key=f"issue:{issue.id}",
            actions=[["Acknowledge", f"ackissue:{issue.id}"]] + ([["Locate", f"locate:{trip.code}"]] if trip else []),
        )
    record(
        db,
        ws,
        "receipt.confirmed",
        actor_name=user.short_name,
        actor_role="store",
        entity=f"order:{order.id}",
        payload={"issues": len(issues)},
        topics=[f"outlet:{order.outlet_id}", "plan"],
    )
    post_notice(
        db,
        ws,
        f"outlet:{order.outlet_id}",
        "Receipt confirmed" + (", issue logged" if issues else ""),
        ("Dispatch has it; a credit note will follow." if issues else "Thank you. Nothing else to do."),
        kind="green" if not issues else "blue",
        icon="check",
    )
    return {"ok": True, "issues": len(issues)}
