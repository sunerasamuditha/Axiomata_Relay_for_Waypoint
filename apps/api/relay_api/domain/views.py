"""Read models: one JSON shape per face. Routers stay thin; the shapes live here."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy.orm import Session, selectinload

from ..models import (
    Deferral,
    Issue,
    Notice,
    Order,
    PendingMove,
    Proof,
    Receipt,
    Stop,
    Trip,
    User,
    VehicleDay,
    Workspace,
)
from .clock import clock_payload, cutoff_for, now_virtual
from .eta import RefCache, live_etas
from .fieldops import DOCK_KINDS
from .handover import pin_check, store_managers
from .planning import current_plan

ACTIVE = ("placed", "confirmed", "planned", "deferred", "loading", "loaded", "out", "delivered", "partial", "failed", "received")


def iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def media_url(mid: str | None) -> str | None:
    return f"/api/media/{mid}" if mid else None


def order_status(o: Order, stop: Stop | None, trip: Trip | None) -> str:
    if o.status in ("cancelled", "received", "deferred", "placed", "confirmed"):
        return o.status
    if stop and stop.status in ("delivered", "partial", "failed", "arrived"):
        return stop.status
    if trip:
        return {"planned": "planned", "loading": "loading", "loaded": "loaded", "out": "out", "done": "delivered"}.get(
            trip.status, "planned"
        )
    return o.status


def notices(db: Session, ws: Workspace, audience: str, limit: int = 30, include_resolved: bool = False) -> list[dict]:
    q = db.query(Notice).filter(Notice.workspace_id == ws.id, Notice.audience == audience)
    if not include_resolved:
        q = q.filter(Notice.resolved.is_(False))
    return [
        {
            "id": n.id,
            "kind": n.kind,
            "icon": n.icon,
            "title": n.title,
            "body": n.body,
            "at": iso(n.at),
            "actions": n.actions or [],
            "entity": n.entity,
            "key": n.key,
            "read": n.read,
        }
        for n in q.order_by(Notice.at.desc(), Notice.id.desc()).limit(limit)
    ]


def _proof_view(p: Proof | None, stop: Stop | None) -> dict | None:
    if not p:
        return None
    return {
        "receiver": p.receiver_name,
        "photo": media_url(p.photo_media_id),
        "signature": media_url(p.signature_media_id),  # proofs from builds before the PIN handover
        "pin_verified": p.pin_verified,
        "at": iso(p.captured_at),
        "note": p.note,
        "simulated": p.simulated,
        "offline": bool(stop and stop.recorded_offline),
        "synced_at": iso(stop.synced_at) if stop else None,
    }


def _lines(o: Order, issues_by_line: dict[int, Issue] | None = None) -> list[dict]:
    out = []
    for ln in o.lines:
        iss = (issues_by_line or {}).get(ln.id)
        out.append(
            {
                "id": ln.id,
                "sku": ln.sku,
                "name": ln.name,
                "qty": ln.qty,
                "uom": ln.uom,
                "load_state": ln.load_state,
                "loaded_qty": ln.loaded_qty,
                "loaded_by": ln.loaded_by,
                "loaded_at": iso(ln.loaded_at),
                "delivered_qty": ln.delivered_qty,
                "received_qty": ln.received_qty,
                "receipt_issue": ln.receipt_issue,
                "flag": {
                    "issue_id": iss.id,
                    "kind": iss.kind,
                    "qty": iss.qty,
                    "status": iss.status,
                    "decision": iss.decision,
                    "decision_text": iss.decision_text,
                    "decided_by": iss.decided_by,
                    "raised_by": iss.raised_by,
                    "raised_at": iso(iss.raised_at),
                }
                if iss
                else None,
            }
        )
    return out


# ------------------------------------------------------------------------------------------------
# dispatcher
# ------------------------------------------------------------------------------------------------


def dispatch_snapshot(db: Session, ws: Workspace) -> dict:
    ref = RefCache.load(db)
    now = now_virtual(ws)
    plan = current_plan(db, ws)
    trips = db.query(Trip).options(selectinload(Trip.stops)).filter_by(plan_id=plan.id).all() if plan else []
    stop_by_order: dict[int, Stop] = {}
    trip_by_id = {t.id: t for t in trips}
    for t in trips:
        for s in t.stops:
            stop_by_order[s.order_id] = s
    orders = (
        db.query(Order)
        .options(selectinload(Order.lines))
        .filter(Order.workspace_id == ws.id, Order.service_date == ws.service_date, Order.status.in_(ACTIVE))
        .order_by(Order.id)
        .all()
    )
    deferrals = {d.order_id: d for d in db.query(Deferral).filter_by(plan_id=plan.id)} if plan else {}
    proofs = {p.stop_id: p for p in db.query(Proof).filter_by(workspace_id=ws.id)}
    receipts = {r.order_id: r for r in db.query(Receipt).filter_by(workspace_id=ws.id)}
    issues = db.query(Issue).filter_by(workspace_id=ws.id).order_by(Issue.id.desc()).limit(60).all()
    issues_by_line = {i.line_id: i for i in issues if i.line_id and i.kind.endswith("dock")}
    pmoves = db.query(PendingMove).filter_by(workspace_id=ws.id).order_by(PendingMove.id.desc()).all()
    pm_by_order = {pm.order_id: pm for pm in pmoves if pm.status == "queued"}
    etas: dict[int, dict] = {}
    for t in trips:
        etas.update(live_etas(t, now))

    ovs = []
    for o in orders:
        s = stop_by_order.get(o.id)
        t = trip_by_id.get(s.trip_id) if s else None
        d = deferrals.get(o.id)
        e = etas.get(s.id) if s else None
        ou = ref.outlets[o.outlet_id]
        ovs.append(
            {
                "id": o.id,
                "ref": o.ref,
                "outlet": o.outlet_id,
                "brand": o.brand,
                "district": ou.district,
                "depot": ou.depot,
                "temp": o.temp,
                "units": o.units,
                "m3": round(o.volume_m3, 3),
                "kg": round(o.weight_kg, 1),
                "status": order_status(o, s, t),
                "trip": t.id if t else None,
                "seq": s.seq if s else None,
                "eta": e["eta"] if e else None,
                "eta_kind": e["kind"] if e else None,
                "band_lo": (e or {}).get("band_lo"),
                "band_hi": (e or {}).get("band_hi"),
                "planned_arrival": iso(s.planned_arrival) if s else None,
                "late_planned": bool(s and s.late_planned),
                "risk": round(s.late_prob, 3) if s else None,
                "svc": s.pred_service_min if s else None,
                "arrived_at": iso(s.arrived_at) if s else None,
                "delivered_at": iso(s.completed_at) if s else None,
                "received": o.id in receipts,
                "receipt": {"at": iso(receipts[o.id].at), "status": receipts[o.id].status, "by": receipts[o.id].by_name}
                if o.id in receipts
                else None,
                "deferred": {"kind": d.kind, "code": d.code, "text": d.text, "streak": d.streak, "cost": d.cost} if d else None,
                "deferred_yesterday": o.deferred_yesterday,
                "days_since": o.days_since_served,
                "placed_at": iso(o.placed_at),
                "channel": o.channel,
                "note": o.note,
                "lines": _lines(o, issues_by_line),
                "proof": _proof_view(proofs.get(s.id), s) if s else None,
                "pending_move": {"id": pm_by_order[o.id].id, "to": pm_by_order[o.id].to_vehicle_id, "trip_no": pm_by_order[o.id].to_trip_no}
                if o.id in pm_by_order
                else None,
            }
        )

    vdays = {vd.vehicle_id: vd for vd in db.query(VehicleDay).filter_by(workspace_id=ws.id, date=ws.service_date)}
    vehicles = []
    for v in ref.vehicles.values():
        vd = vdays.get(v.id)
        vehicles.append(
            {
                "id": v.id,
                "type": v.type,
                "temp": v.temp,
                "kg": v.weight_cap_kg,
                "m3": v.volume_cap_m3,
                "kmpl": v.km_per_l,
                "quota": v.weekly_fuel_quota_l,
                "depot": v.depot,
                "status": "workshop" if vd and vd.status == "in_workshop" else "ok",
                "driver": v.driver_name,
                "fuel_used": round(vd.fuel_used_l, 1) if vd else 0.0,
                "back_on": vd.back_on.isoformat() if vd and vd.back_on else None,
            }
        )
    tvs = []
    for t in sorted(trips, key=lambda x: (x.depot, x.planned_depart, x.vehicle_id)):
        stops = sorted(t.stops, key=lambda s: s.seq)
        tvs.append(
            {
                "id": t.id,
                "code": t.code,
                "vehicle": t.vehicle_id,
                "n": t.trip_no,
                "brand": t.brand,
                "district": t.district,
                "depot": t.depot,
                "status": t.status,
                "depart": iso(t.planned_depart),
                "return": iso(t.planned_return),
                "minutes": t.minutes,
                "km": t.km,
                "litres": t.litres,
                "m3": round(t.volume_m3, 3),
                "kg": round(t.weight_kg, 1),
                "locked": t.locked,
                "controlled": t.controlled,
                "signal": t.signal,
                "dark_since": iso(t.dark_since),
                "last_contact": iso(t.last_contact_at),
                "released_at": iso(t.released_at),
                "departed_at": iso(t.departed_at),
                "completed_at": iso(t.completed_at),
                "seal": t.seal_no,
                "temp_c": t.release_temp_c,
                "dock": t.dock,
                "bay": t.bay,
                "driver": ref.vehicles[t.vehicle_id].driver_name,
                "stops": [s.order_id for s in stops],
                "loading": {
                    "done": sum(1 for s in stops for ln in s.order.lines if ln.load_state != "todo"),
                    "total": sum(len(s.order.lines) for s in stops),
                },
            }
        )
    phase = "ordering"
    if ws.orders_closed_at:
        phase = "closed"
    if plan:
        phase = plan.status
    next_day = ws.service_date + timedelta(days=1)
    queue_orders = (
        db.query(Order)
        .filter(
            Order.workspace_id == ws.id,
            Order.service_date == (ws.service_date if phase == "ordering" else next_day),
            Order.status.in_(("placed", "confirmed")),
        )
        .order_by(Order.placed_at.desc())
        .all()
    )
    return {
        "clock": clock_payload(ws),
        "phase": phase,
        "service_date": ws.service_date.isoformat(),
        "plan": {
            "id": plan.id,
            "version": plan.version,
            "policy": plan.policy,
            "status": plan.status,
            "created_at": iso(plan.created_at),
            "published_at": iso(plan.published_at),
            "kpis": plan.kpis,
            "solver": plan.solver,
            "created_by": plan.created_by,
        }
        if plan
        else None,
        "vehicles": vehicles,
        "trips": tvs,
        "orders": ovs,
        "issues": [_issue_view(i) for i in issues],
        "feed": notices(db, ws, "dispatch", 40),
        "pending_moves": [
            {
                "id": pm.id,
                "order_id": pm.order_id,
                "to": pm.to_vehicle_id,
                "trip_no": pm.to_trip_no,
                "status": pm.status,
                "resolution": pm.resolution,
                "at": iso(pm.created_at),
            }
            for pm in pmoves[:20]
        ],
        "queue": {
            "date": (ws.service_date if phase == "ordering" else next_day).isoformat(),
            "cutoff": cutoff_for(ws.service_date if phase == "ordering" else next_day).isoformat(),
            "orders": [_queue_row(o) for o in queue_orders],
        },
    }


def _queue_row(o: Order) -> dict:
    ou = RefCache.outlets[o.outlet_id]
    flag = ""
    if o.volume_m3 > 38:
        flag = "Too big for any vehicle"
    elif ou.parking == "van_only":
        flag = "Van-only outlet"
    return {
        "id": o.id,
        "ref": o.ref,
        "outlet": o.outlet_id,
        "name": ou.name,
        "district": ou.district,
        "brand": o.brand,
        "temp": o.temp,
        "units": o.units,
        "m3": round(o.volume_m3, 2),
        "kg": round(o.weight_kg),
        "placed_at": iso(o.placed_at),
        "channel": o.channel,
        "status": o.status,
        "flag": flag,
        "carry_over": o.channel == "carryover",
        "note": o.note,
    }


def _issue_view(i: Issue) -> dict:
    return {
        "id": i.id,
        "kind": i.kind,
        "severity": i.severity,
        "status": i.status,
        "trip": i.trip_id,
        "stop": i.stop_id,
        "order": i.order_id,
        "line": i.line_id,
        "qty": i.qty,
        "note": i.note,
        "raised_by": i.raised_by,
        "raised_role": i.raised_role,
        "raised_at": iso(i.raised_at),
        "decision": i.decision,
        "decision_text": i.decision_text,
        "decided_by": i.decided_by,
        "decided_at": iso(i.decided_at),
        "photo": media_url(i.photo_media_id),
        "payload": i.payload,
    }


# ------------------------------------------------------------------------------------------------
# dock
# ------------------------------------------------------------------------------------------------

# The loading crew on each dock's shared tablet (fictional names). Whoever ticks a line is
# recorded against it, so the dispatcher sees who loaded what without a login per person.
DOCK_CREW = {
    "Kandy": [
        ("Kasun Bandara", "Kasun", "KB", "#6B4DFF", 2),
        ("Sameera Perera", "Sameera", "SP", "#0E8A7A", 3),
        ("Dinesh Kumara", "Dinesh", "DK", "#1438D1", 4),
        ("Tharindu Wijesinghe", "Tharindu", "TW", "#C9391A", 5),
    ],
    "Peliyagoda": [
        ("Ravindran Selvam", "Ravindran", "RS", "#C2410C", 1),
        ("Chaminda Silva", "Chaminda", "CS", "#0E8A7A", 2),
        ("Nimal Jayasinghe", "Nimal", "NJ", "#1438D1", 3),
        ("Ishara Madushani", "Ishara", "IM", "#6B4DFF", 4),
    ],
}


def _dispatcher_short(db: Session) -> str:
    u = db.query(User).filter_by(role="dispatcher").order_by(User.id).first()
    return u.short_name if u else "the dispatcher"


def dock_queue(db: Session, ws: Workspace, user: User) -> dict:
    ref = RefCache.load(db)
    plan = current_plan(db, ws)
    published = plan if plan and plan.status == "published" else None
    trips = db.query(Trip).options(selectinload(Trip.stops)).filter_by(plan_id=published.id, depot=user.depot).all() if published else []
    open_issues = defaultdict(int)
    for i in db.query(Issue).filter(Issue.workspace_id == ws.id, Issue.status == "open", Issue.kind.in_(DOCK_KINDS)):
        open_issues[i.trip_id] += 1
    rows = []
    for t in sorted(trips, key=lambda x: (x.planned_depart, x.vehicle_id)):
        v = ref.vehicles[t.vehicle_id]
        lines = [ln for s in t.stops for ln in s.order.lines]
        done = [ln for ln in lines if ln.load_state != "todo"]
        crew = max((ln for ln in done if ln.loaded_at), key=lambda ln: ln.loaded_at, default=None)
        rows.append(
            {
                "id": t.id,
                "code": t.code,
                "vehicle": t.vehicle_id,
                "type": v.type,
                "temp": v.temp,
                "cap_kg": v.weight_cap_kg,
                "cap_m3": v.volume_cap_m3,
                "trip_no": t.trip_no,
                "brand": t.brand,
                "district": t.district,
                "n_stops": len({s.order.outlet_id for s in t.stops}),
                "depart": iso(t.planned_depart),
                "status": t.status,
                "bay": t.bay,
                "dock": t.dock,
                "lines_total": len(lines),
                "lines_done": len(done),
                "flags_open": open_issues.get(t.id, 0),
                "crew": crew.loaded_by if crew else None,
                "driver": v.driver_name,
                "controlled": t.controlled,
                "has_chilled": any(s.order.temp == "chilled" for s in t.stops),
            }
        )
    return {
        "clock": clock_payload(ws),
        "depot": user.depot,
        "dock": user.dock,
        "plan": {"version": published.version, "published_at": iso(published.published_at), "policy": published.policy}
        if published
        else None,
        "pending_plan": bool(plan and plan.status == "draft"),
        "trips": rows,
        "changes": notices(db, ws, f"depot:{user.depot}", 20),
        "crew_roster": [
            {"name": n, "short": sh, "initials": ini, "color": col, "bay": bay}
            for n, sh, ini, col, bay in DOCK_CREW.get(user.depot or "", [])
        ],
        "dispatcher": _dispatcher_short(db),
        "depot_name": "Kandy Hub" if user.depot == "Kandy" else f"{user.depot} DC",
    }


def dock_trip(db: Session, ws: Workspace, user: User, trip_id: int) -> dict | None:
    ref = RefCache.load(db)
    t = db.get(Trip, trip_id)
    if not t or t.workspace_id != ws.id or t.depot != user.depot:
        return None
    v = ref.vehicles[t.vehicle_id]
    issues = db.query(Issue).filter_by(trip_id=t.id).order_by(Issue.id).all()
    by_line = {i.line_id: i for i in issues if i.line_id}
    stops = sorted(t.stops, key=lambda s: s.seq)
    visits: list[dict] = []
    for s in stops:
        o = s.order
        ou = ref.outlets[o.outlet_id]
        visits.append(
            {
                "stop_id": s.id,
                "seq": s.seq + 1,
                "order_id": o.id,
                "ref": o.ref,
                "outlet": o.outlet_id,
                "name": ou.name,
                "temp": o.temp,
                "units": o.units,
                "m3": round(o.volume_m3, 2),
                "kg": round(o.weight_kg),
                "window": [ou.window_open, ou.window_close],
                "dock_type": ou.dock_type,
                "eta": iso(s.planned_arrival),
                "manager": ou.manager,
                "lines": _lines(o, by_line),
            }
        )
    load_order = list(reversed(visits))
    lines = [ln for vv in visits for ln in vv["lines"]]
    return {
        "clock": clock_payload(ws),
        "trip": {
            "id": t.id,
            "code": t.code,
            "vehicle": t.vehicle_id,
            "type": v.type,
            "temp": v.temp,
            "cap_kg": v.weight_cap_kg,
            "cap_m3": v.volume_cap_m3,
            "trip_no": t.trip_no,
            "brand": t.brand,
            "district": t.district,
            "status": t.status,
            "depart": iso(t.planned_depart),
            "bay": t.bay,
            "dock": t.dock,
            "driver": v.driver_name,
            "released_at": iso(t.released_at),
            "released_by": t.released_by,
            "seal": t.seal_no,
            "temp_c": t.release_temp_c,
            "doors_ok": t.doors_ok,
            "m3": round(t.volume_m3, 2),
            "kg": round(t.weight_kg),
            "needs_temp": v.temp == "reefer" and any(s.order.temp == "chilled" for s in stops),
        },
        "dispatcher": _dispatcher_short(db),
        "load_order": load_order,
        "progress": {
            "total": len(lines),
            "done": sum(1 for ln in lines if ln["load_state"] != "todo"),
            "waiting": sum(1 for ln in lines if ln["flag"] and ln["flag"]["status"] == "open"),
            "m3": round(sum(vv["m3"] for vv in visits), 2),
            "kg": round(sum(vv["kg"] for vv in visits)),
        },
        "issues": [_issue_view(i) for i in issues],
    }


# ------------------------------------------------------------------------------------------------
# driver
# ------------------------------------------------------------------------------------------------


def driver_run(db: Session, ws: Workspace, user: User) -> dict:
    ref = RefCache.load(db)
    now = now_virtual(ws)
    plan = current_plan(db, ws)
    published = plan if plan and plan.status == "published" else None
    v = ref.vehicles.get(user.vehicle_id or "")
    trips = (
        db.query(Trip)
        .options(selectinload(Trip.stops))
        .filter_by(plan_id=published.id, vehicle_id=user.vehicle_id)
        .order_by(Trip.trip_no)
        .all()
        if published
        else []
    )
    proofs = {p.stop_id: p for p in db.query(Proof).filter(Proof.stop_id.in_([s.id for t in trips for s in t.stops]))} if trips else {}
    issues = db.query(Issue).filter(Issue.trip_id.in_([t.id for t in trips])).all() if trips else []
    by_line = {i.line_id: i for i in issues if i.line_id}
    managers = store_managers(db) if trips else {}
    out_trips = []
    for t in trips:
        etas = live_etas(t, now)
        stops = sorted(t.stops, key=lambda s: s.seq)
        visits: list[dict] = []
        for s in stops:
            o = s.order
            ou = ref.outlets[o.outlet_id]
            if visits and visits[-1]["outlet"] == o.outlet_id:
                vv = visits[-1]
            else:
                mgr = managers.get(o.outlet_id)
                vv = {
                    "visit_id": s.id,
                    "stop_ids": [],
                    "seq": len(visits) + 1,
                    "outlet": o.outlet_id,
                    "name": ou.name,
                    "district": ou.district,
                    "dock_type": ou.dock_type,
                    "parking": ou.parking,
                    "window": [ou.window_open, ou.window_close],
                    "eta": etas[s.id]["eta"],
                    "eta_kind": etas[s.id]["kind"],
                    "band_lo": etas[s.id].get("band_lo"),
                    "band_hi": etas[s.id].get("band_hi"),
                    "planned_arrival": iso(s.planned_arrival),
                    "late_prob": s.late_prob,
                    "status": s.status,
                    "orders": [],
                    "contact": ou.manager,
                    # the store manager confirms the handover with their PIN; the phone checks it
                    # offline against this one-way value (never the PIN itself)
                    "pin_required": mgr is not None,
                    "pin_check": pin_check(s.id, mgr.delivery_pin) if mgr and mgr.delivery_pin else None,
                    "arrived_at": iso(s.arrived_at),
                    "completed_at": iso(s.completed_at),
                    "proof": _proof_view(proofs.get(s.id), s),
                }
                visits.append(vv)
            vv["stop_ids"].append(s.id)
            vv["late_prob"] = max(vv["late_prob"], s.late_prob)
            if s.status != vv["status"]:
                order_rank = ["pending", "arrived", "partial", "failed", "delivered"]
                vv["status"] = min(vv["status"], s.status, key=lambda x: order_rank.index(x) if x in order_rank else 0)
            vv["orders"].append(
                {"order_id": o.id, "ref": o.ref, "temp": o.temp, "units": o.units, "m3": round(o.volume_m3, 2), "lines": _lines(o, by_line)}
            )
        all_lines = [ln for s in stops for ln in s.order.lines]
        loaded = [ln for ln in all_lines if ln.load_state != "todo"]
        last_loader = max((ln for ln in loaded if ln.loaded_at), key=lambda ln: ln.loaded_at, default=None)
        out_trips.append(
            {
                "id": t.id,
                "code": t.code,
                "trip_no": t.trip_no,
                "status": t.status,
                "dock": t.dock,
                "bay": t.bay,
                "lines_total": len(all_lines),
                "lines_done": len(loaded),
                "loader": last_loader.loaded_by if last_loader else None,
                "brand": t.brand,
                "district": t.district,
                "depot": t.depot,
                "depart": iso(t.planned_depart),
                "return": iso(t.planned_return),
                "released_at": iso(t.released_at),
                "released_by": t.released_by,
                "seal": t.seal_no,
                "temp_c": t.release_temp_c,
                "departed_at": iso(t.departed_at),
                "signal": t.signal,
                "visits": visits,
                "km": t.km,
                "minutes": t.minutes,
            }
        )
    return {
        "clock": clock_payload(ws),
        "vehicle": {"id": v.id, "type": v.type, "temp": v.temp, "kg": v.weight_cap_kg, "m3": v.volume_cap_m3, "depot": v.depot}
        if v
        else None,
        "driver": {"name": user.name, "short": user.short_name, "lang": user.lang},
        "dispatcher": _dispatcher_short(db),
        "depot_name": ("Kandy Hub" if v.depot == "Kandy" else f"{v.depot} DC") if v else "",
        "plan": {"version": published.version, "published_at": iso(published.published_at)} if published else None,
        "pending_plan": bool(plan and plan.status != "published") or not plan,
        "trips": out_trips,
        "notices": notices(db, ws, f"vehicle:{user.vehicle_id}", 20),
        "known_issues": [_issue_view(i) for i in issues],
    }


# ------------------------------------------------------------------------------------------------
# store
# ------------------------------------------------------------------------------------------------

STEPS = ["placed", "planned", "loaded", "out", "delivered", "received"]


def relay_steps(status: str) -> list[dict]:
    labels = {
        "placed": "Ordered",
        "planned": "Planned",
        "loaded": "Loaded",
        "out": "On road",
        "delivered": "Delivered",
        "received": "Received",
    }
    if status == "deferred":
        return [
            {
                "key": k,
                "label": "Deferred" if k == "planned" else labels[k],
                "state": "done" if k == "placed" else ("warn" if k == "planned" else ""),
            }
            for k in STEPS
        ]
    idx = {
        "placed": 0,
        "confirmed": 0,
        "planned": 1,
        "loading": 1,
        "loaded": 2,
        "out": 3,
        "arrived": 3,
        "delivered": 4,
        "partial": 4,
        "failed": 4,
        "received": 5,
    }.get(status, 0)
    now_step = {"loading": 2, "out": 3, "arrived": 4}.get(status)
    out = []
    for i, k in enumerate(STEPS):
        state = "done" if i <= idx else ""
        if now_step is not None and i == now_step:
            state = "now"
        if k == "delivered" and status in ("partial", "failed") and i <= idx:
            state = "warn"
        out.append({"key": k, "label": ("Partial" if status == "partial" and k == "delivered" else labels[k]), "state": state})
    return out


def store_order_view(db: Session, ws: Workspace, o: Order, now: datetime) -> dict:
    ref = RefCache.load(db)
    ou = ref.outlets[o.outlet_id]
    s = db.query(Stop).filter_by(order_id=o.id).first()
    plan = current_plan(db, ws)
    if s and plan and s.trip.plan_id != plan.id:
        s = None
    t = s.trip if s else None
    published = bool(t and plan and plan.status == "published")
    status = order_status(o, s, t)
    if status in ("planned", "deferred") and not published and o.service_date == ws.service_date and plan and plan.status == "draft":
        status = "confirmed"
    e = live_etas(t, now).get(s.id) if (s and published) else None
    d = db.query(Deferral).filter_by(order_id=o.id).order_by(Deferral.id.desc()).first()
    r = db.query(Receipt).filter_by(order_id=o.id).first()
    p = db.query(Proof).filter_by(stop_id=s.id).first() if s else None
    shorts = db.query(Issue).filter(Issue.order_id == o.id, Issue.kind.in_(DOCK_KINDS)).all()
    visit_seq = n_visits = pos = None
    if t and published:
        outlets_in_order = []
        for st in sorted(t.stops, key=lambda x: x.seq):
            if not outlets_in_order or outlets_in_order[-1] != st.order.outlet_id:
                outlets_in_order.append(st.order.outlet_id)
        visit_seq = outlets_in_order.index(o.outlet_id) + 1 if o.outlet_id in outlets_in_order else None
        n_visits = len(outlets_in_order)
        done_outlets = {st.order.outlet_id for st in t.stops if st.status in ("delivered", "partial", "failed")}
        pos = sum(1 for x in outlets_in_order if x in done_outlets)
    return {
        "id": o.id,
        "ref": o.ref,
        "temp": o.temp,
        "brand": o.brand,
        "units": o.units,
        "m3": round(o.volume_m3, 2),
        "kg": round(o.weight_kg),
        "service_date": o.service_date.isoformat(),
        "status": status,
        "steps": relay_steps(status),
        "channel": o.channel,
        "placed_at": iso(o.placed_at),
        "window": [ou.window_open, ou.window_close],
        "note": o.note,
        "eta": e["eta"] if e else None,
        "eta_kind": e["kind"] if e else None,
        "band_lo": (e or {}).get("band_lo"),
        "band_hi": (e or {}).get("band_hi"),
        "vehicle": t.vehicle_id if t and published else None,
        "driver": ref.vehicles[t.vehicle_id].driver_name if t and published else None,
        "trip_status": t.status if t and published else None,
        "signal": t.signal if t and published else None,
        "dark_since": iso(t.dark_since) if t and published else None,
        "visit": visit_seq,
        "visits": n_visits,
        "visits_done": pos,
        "departed_at": iso(t.departed_at) if t and published else None,
        "arrived_at": iso(s.arrived_at) if s else None,
        "delivered_at": iso(s.completed_at) if s else None,
        "late_prob": s.late_prob if s and published else None,
        "proof": _proof_view(p, s),
        "deferral": {"code": d.code, "text": d.text, "streak": d.streak, "next_date": d.next_date.isoformat() if d.next_date else None}
        if d and status == "deferred"
        else None,
        "carried_from": o.note if o.channel == "carryover" else None,
        "deferred_yesterday": o.deferred_yesterday,
        "receipt": {"at": iso(r.at), "by": r.by_name, "status": r.status, "note": r.note} if r else None,
        "shortfalls": [
            {
                "qty": i.qty,
                "line": next((ln.name for ln in o.lines if ln.id == i.line_id), ""),
                "decision_text": i.decision_text,
                "status": i.status,
            }
            for i in shorts
        ],
        "lines": _lines(o),
    }


def store_profile(db: Session, user: User) -> dict:
    """The store manager's own account: the only response that carries their delivery PIN."""
    ou = RefCache.load(db).outlets.get(user.outlet_id or "")
    return {
        "name": user.name,
        "email": user.email,
        "title": user.title,
        "outlet": {"id": user.outlet_id, "name": ou.name if ou else ""},
        "delivery_pin": user.delivery_pin,
    }


def store_home(db: Session, ws: Workspace, user: User) -> dict:
    ref = RefCache.load(db)
    now = now_virtual(ws)
    ou = ref.outlets[user.outlet_id]
    from .ordering import open_service_date

    open_day = open_service_date(ws)
    orders = (
        db.query(Order)
        .filter(Order.workspace_id == ws.id, Order.outlet_id == user.outlet_id, Order.status != "cancelled")
        .order_by(Order.service_date.desc(), Order.id)
        .all()
    )
    days: dict[str, list[dict]] = defaultdict(list)
    for o in orders:
        days[o.service_date.isoformat()].append(store_order_view(db, ws, o, now))
    focus = ws.service_date.isoformat()
    previous = (ws.service_date - timedelta(days=1)).isoformat()
    upcoming = [d for d in sorted(days) if d > focus]
    return {
        "clock": clock_payload(ws),
        "outlet": {
            "id": ou.id,
            "name": ou.name,
            "brand": ou.brand,
            "district": ou.district,
            "depot": ou.depot,
            "dock_type": ou.dock_type,
            "parking": ou.parking,
            "window": [ou.window_open, ou.window_close],
        },
        "manager": {"name": user.name, "short": user.short_name},
        "focus_date": focus,
        "deliveries": days.get(focus, []),
        "previous_date": previous,
        "previous": days.get(previous, []),
        "upcoming": [{"date": d, "orders": days[d]} for d in upcoming],
        "ordering": {
            "date": open_day.isoformat(),
            "cutoff": cutoff_for(open_day).isoformat(),
            "open": now < cutoff_for(open_day) and not (open_day == ws.service_date and ws.orders_closed_at),
        },
        "notices": notices(db, ws, f"outlet:{user.outlet_id}", 30, include_resolved=True),
    }
