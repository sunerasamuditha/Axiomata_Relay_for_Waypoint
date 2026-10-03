"""Planning service: database <-> relay_engine.

close_orders()   cutoff: confirm the day's orders, place standing orders nobody changed
run_plan()       build the engine problem from the database, solve, persist a draft plan
publish()        send the plan to every face (dock, drivers, stores) with notices
lever()          what each deferral policy would do, relative to the current plan
apply_policy()   re-plan under another policy (before loading starts)
move_order()     dispatcher drag-and-drop, validated with the engine's rules (server side)
defer_order()    dispatcher defers with a reason
"""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta

from relay_engine import POLICY_LABEL, Problem, Solution, TripPlan, check_move, trip_km, trip_litres, trip_minutes
from relay_engine import Order as EOrder
from relay_engine import Vehicle as EVehicle
from relay_engine import plan as engine_plan
from relay_engine.core import District as EDistrict
from relay_engine.core import Pin, parse_window
from relay_engine.timetable import timetable_vehicle
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Deferral, Notice, Order, Plan, StandingOrder, Stop, Trip, User, VehicleDay, Workspace
from .clock import at_minutes, cutoff_for, now_virtual
from .eta import RefCache, predict_trip
from .events import notify, post_notice, record, resolve_notices

ACTIVE_ORDER_STATES = ("confirmed", "planned", "deferred")


class PlanningError(Exception):
    pass


def depot_code(depot: str) -> str:
    return "P" if depot == "Peliyagoda" else "K"


def next_ref(db: Session, ws: Workspace) -> str:
    n = db.scalar(select(func.count()).select_from(Order).where(Order.workspace_id == ws.id)) or 0
    return f"WP-{30401 + n}"


def current_plan(db: Session, ws: Workspace, service_date: date | None = None) -> Plan | None:
    d = service_date or ws.service_date
    return (
        db.query(Plan)
        .filter(Plan.workspace_id == ws.id, Plan.service_date == d, Plan.status.in_(("draft", "published")))
        .order_by(Plan.version.desc())
        .first()
    )


def published_plan(db: Session, ws: Workspace) -> Plan | None:
    return (
        db.query(Plan).filter_by(workspace_id=ws.id, service_date=ws.service_date, status="published").order_by(Plan.version.desc()).first()
    )


# ------------------------------------------------------------------------------------------------
# cutoff
# ------------------------------------------------------------------------------------------------


def close_orders(db: Session, ws: Workspace, actor: str) -> dict:
    if ws.orders_closed_at:
        raise PlanningError("Orders for this day are already closed.")
    now = now_virtual(ws)
    closed_at = max(now, cutoff_for(ws.service_date)) if now >= cutoff_for(ws.service_date) else now
    from .ordering import place_order  # local import: ordering depends on this module's helpers

    placed = []
    for so in db.query(StandingOrder).filter_by(workspace_id=ws.id):
        exists = (
            db.query(Order)
            .filter(
                Order.workspace_id == ws.id,
                Order.outlet_id == so.outlet_id,
                Order.service_date == ws.service_date,
                Order.temp == so.temp,
                Order.channel.in_(("app", "phone", "standing")),
                Order.status != "cancelled",
            )
            .first()
        )
        if not exists:
            o = place_order(db, ws, so.outlet_id, so.temp, [(s, q) for s, q in so.lines], channel="standing", by=None, at=closed_at)
            placed.append(o)
    n = 0
    for o in db.query(Order).filter(Order.workspace_id == ws.id, Order.service_date == ws.service_date, Order.status == "placed"):
        o.status = "confirmed"
        n += 1
    ws.orders_closed_at = closed_at
    for notice in db.query(Notice).filter(Notice.workspace_id == ws.id, Notice.key == "cutoff", Notice.resolved.is_(False)):
        notice.resolved = True
    total = (
        db.query(Order)
        .filter(Order.workspace_id == ws.id, Order.service_date == ws.service_date, Order.status.in_(ACTIVE_ORDER_STATES))
        .count()
    )
    record(
        db,
        ws,
        "orders.closed",
        actor_name=actor,
        actor_role="dispatcher",
        entity=f"day:{ws.service_date}",
        payload={"confirmed": n, "standing_placed": [o.ref for o in placed], "total": total},
        topics=["plan", "outlet:*"],
    )
    for o in placed:
        post_notice(
            db,
            ws,
            f"outlet:{o.outlet_id}",
            "Your usual order was placed at the cutoff",
            f"{o.ref} · {o.units} cases of dry goods for {ws.service_date:%a %-d %b}. Nothing else to do.",
            kind="blue",
            icon="check",
        )
    return {"confirmed": n, "standing_placed": len(placed), "total": total, "closed_at": closed_at.isoformat()}


# ------------------------------------------------------------------------------------------------
# problem construction
# ------------------------------------------------------------------------------------------------


@dataclass
class Built:
    problem: Problem
    orders: dict[int, Order]


def build_problem(db: Session, ws: Workspace, policy: str, *, extra_pins: list[Pin] | None = None) -> Built:
    ref = RefCache.load(db)
    s = get_settings()
    orders = (
        db.query(Order)
        .filter(Order.workspace_id == ws.id, Order.service_date == ws.service_date, Order.status.in_(ACTIVE_ORDER_STATES))
        .order_by(Order.id)
        .all()
    )
    eorders = []
    for o in orders:
        ou = ref.outlets[o.outlet_id]
        eorders.append(
            EOrder(
                id=str(o.id),
                outlet_id=o.outlet_id,
                brand=o.brand,
                district=ou.district,
                depot=ou.depot,
                dock_type=ou.dock_type,
                parking=ou.parking,
                temp=o.temp,
                weight_kg=o.weight_kg,
                volume_m3=o.volume_m3,
                window_open=_hm(ou.window_open),
                window_close=_hm(ou.window_close),
                mall_window=parse_window(ou.mall_window),
                deferred_yesterday=o.deferred_yesterday,
                days_since_served=o.days_since_served,
                usual_vehicle=ou.usual_vehicle_chilled if o.temp == "chilled" else ou.usual_vehicle_ambient,
                label=ou.name,
            )
        )
    vdays = {vd.vehicle_id: vd for vd in db.query(VehicleDay).filter_by(workspace_id=ws.id, date=ws.service_date)}
    evehicles = []
    for v in ref.vehicles.values():
        vd = vdays.get(v.id)
        evehicles.append(
            EVehicle(
                id=v.id,
                type=v.type,
                temp=v.temp,
                weight_cap_kg=v.weight_cap_kg,
                volume_cap_m3=v.volume_cap_m3,
                km_per_l=v.km_per_l,
                fuel_left_l=max(0.0, v.weekly_fuel_quota_l - (vd.fuel_used_l if vd else 0.0)),
                depot=v.depot,
                available=(vd is None or vd.status == "available"),
                driver=v.driver_name,
            )
        )
    districts = {d.name: EDistrict(d.name, d.depot, d.d2d_min, d.inter_min, d.d2d_km, d.inter_km) for d in ref.districts.values()}
    pins = list(extra_pins or [])
    plan = current_plan(db, ws)
    if plan:
        for t in db.query(Trip).filter_by(plan_id=plan.id):
            if t.locked or t.status != "planned":
                pins += [Pin(str(st.order_id), t.vehicle_id, t.trip_no) for st in t.stops]
    problem = Problem(
        orders=eorders,
        vehicles=evehicles,
        districts=districts,
        allowance=dict(ref.allowance),
        policy=policy,  # type: ignore[arg-type]
        pins=pins,
        time_limit_s=s.solver_time_limit_s,
        workers=8,
    )
    return Built(problem, {o.id: o for o in orders})


def _hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def solve(db: Session, ws: Workspace, policy: str) -> tuple[Solution, Built]:
    built = build_problem(db, ws, policy)
    if not built.problem.orders:
        raise PlanningError("There are no confirmed orders to plan. Close orders first.")
    sol = engine_plan(built.problem, explain_budget_s=get_settings().explain_budget_s)
    return sol, built


# ------------------------------------------------------------------------------------------------
# persisting a plan
# ------------------------------------------------------------------------------------------------


def _any_started(db: Session, plan: Plan | None) -> bool:
    if not plan:
        return False
    return db.query(Trip).filter(Trip.plan_id == plan.id, Trip.status != "planned").count() > 0


def driver_vehicles(db: Session) -> set[str]:
    return {u.vehicle_id for u in db.query(User).filter(User.role == "driver", User.vehicle_id.isnot(None))}


def persist(db: Session, ws: Workspace, sol: Solution, built: Built, actor: str, *, publish_now: bool = False, note: str = "") -> Plan:
    prev = current_plan(db, ws)
    if _any_started(db, prev):
        raise PlanningError("Loading has started, so the plan is frozen. Move or defer orders one at a time instead.")
    version = (db.scalar(select(func.max(Plan.version)).where(Plan.workspace_id == ws.id, Plan.service_date == ws.service_date)) or 0) + 1
    if prev:
        for t in db.query(Trip).filter_by(plan_id=prev.id).all():
            db.delete(t)
        db.query(Deferral).filter_by(plan_id=prev.id).delete()
        prev.status = "superseded"
        db.flush()
    now = now_virtual(ws)
    plan = Plan(
        workspace_id=ws.id,
        service_date=ws.service_date,
        version=version,
        policy=sol.policy,
        status="draft",
        created_at=now,
        created_by=actor,
        kpis=sol.kpis,
        solver={
            "status": sol.status,
            "solve_ms": sol.solve_ms,
            "objective": sol.objective,
            "bound": sol.bound,
            "depots": sol.kpis.get("depot_status", {}),
            "model": "CP-SAT (OR-Tools)",
            "predictor": __import__("relay_ml").get_predictor().describe(),
        },
        note=note,
    )
    db.add(plan)
    db.flush()
    humans = driver_vehicles(db)
    ref = RefCache.load(db)
    by_depot: dict[str, list[TripPlan]] = defaultdict(list)
    for tp in sol.trips:
        by_depot[tp.depot].append(tp)
    order_trip: dict[int, Trip] = {}
    for depot, tps in by_depot.items():
        tps.sort(key=lambda t: (t.depart or 0, t.vehicle_id, t.trip_no))
        for i, tp in enumerate(tps):
            dock = 1 if depot == "Kandy" else (i // 5) % 3 + 1
            trip = Trip(
                workspace_id=ws.id,
                plan_id=plan.id,
                service_date=ws.service_date,
                code=f"{depot_code(depot)}{i + 1}",
                vehicle_id=tp.vehicle_id,
                trip_no=tp.trip_no,
                depot=depot,
                brand=tp.brand,
                district=tp.district,
                status="planned",
                planned_depart=at_minutes(ws.service_date, tp.depart or 0),
                planned_return=at_minutes(ws.service_date, tp.return_at or 0),
                minutes=tp.minutes,
                km=tp.km,
                litres=tp.litres,
                volume_m3=tp.volume_m3,
                weight_kg=tp.weight_kg,
                dock=dock,
                bay=i % 5 + 1,
                controlled="human" if tp.vehicle_id in humans else "sim",
            )
            db.add(trip)
            db.flush()
            for seq, st in enumerate(tp.stops):
                oid = int(st.order_id)
                db.add(
                    Stop(
                        workspace_id=ws.id,
                        trip_id=trip.id,
                        order_id=oid,
                        seq=seq,
                        planned_arrival=at_minutes(ws.service_date, st.arrive),
                        planned_start=at_minutes(ws.service_date, st.start),
                        planned_finish=at_minutes(ws.service_date, st.finish),
                        late_planned=st.late,
                    )
                )
                order_trip[oid] = trip
            db.flush()
            db.refresh(trip)
            predict_trip(db, trip, built.orders)
    for oid, o in built.orders.items():
        if oid in order_trip:
            o.status = "planned"
        else:
            o.status = "deferred"
    for d in sol.deferred:
        o = built.orders[int(d.order_id)]
        names = {}
        if d.cost.get("displaced"):
            names = {
                "displaced_names": [ref.outlets[built.orders[int(x)].outlet_id].name for x in d.cost["displaced"] if int(x) in built.orders]
            }
        db.add(
            Deferral(
                workspace_id=ws.id,
                plan_id=plan.id,
                order_id=o.id,
                kind=d.kind,
                code=d.code,
                text=d.text,
                cost={**d.cost, **names},
                streak=o.deferral_streak + 1,
                created_at=now,
                next_date=ws.service_date + timedelta(days=1),
            )
        )
    k = sol.kpis
    unavoidable = sum(1 for d in sol.deferred if d.kind == "unavoidable")
    record(
        db,
        ws,
        "plan.drafted",
        actor_name=actor,
        actor_role="dispatcher",
        entity=f"plan:{plan.id}",
        payload={"version": version, "policy": sol.policy, "served": k["served"], "deferred": k["deferred"], "ms": sol.solve_ms},
        topics=["plan"],
    )
    post_notice(
        db,
        ws,
        "dispatch",
        f"Plan v{version} drafted · {POLICY_LABEL[sol.policy]}",
        f"{k['served']} of {k['orders']} orders on {k['trips']} trips. {k['deferred']} deferred ({unavoidable} unavoidable). "
        f"Solved in {sol.solve_ms / 1000:.1f} s.",
        kind="blue",
        icon="layers",
        actions=[["Open lever", "dview:lever"]],
        key="plan",
    )
    if publish_now:
        publish(db, ws, plan, actor)
    return plan


def publish(db: Session, ws: Workspace, plan: Plan, actor: str) -> Plan:
    for p in db.query(Plan).filter(
        Plan.workspace_id == ws.id, Plan.service_date == plan.service_date, Plan.status == "published", Plan.id != plan.id
    ):
        p.status = "superseded"
    plan.status = "published"
    plan.published_at = now_virtual(ws)
    ref = RefCache.load(db)
    trips = db.query(Trip).filter_by(plan_id=plan.id).all()
    day = f"{plan.service_date:%a %-d %b}"
    # docks
    for depot in {t.depot for t in trips}:
        ts = [t for t in trips if t.depot == depot]
        first = min(ts, key=lambda t: t.planned_depart)
        post_notice(
            db,
            ws,
            f"depot:{depot}",
            f"Plan v{plan.version} is live · {len(ts)} trucks for {day}",
            f"First departure {first.vehicle_id} at {first.planned_depart:%H:%M}. Load in reverse stop order.",
            kind="blue",
            icon="layers",
            key="plan",
        )
    # drivers
    for t in trips:
        if t.controlled == "human":
            post_notice(
                db,
                ws,
                f"vehicle:{t.vehicle_id}",
                f"Your run for {day} is planned",
                f"Trip {t.trip_no}: {t.district}, {len(t.stops)} stops, departs {t.planned_depart:%H:%M} from {t.depot}.",
                kind="blue",
                icon="route",
                key=f"plan:{t.trip_no}",
            )
    # stores
    deferrals = {d.order_id: d for d in db.query(Deferral).filter_by(plan_id=plan.id)}
    stops = {s.order_id: s for t in trips for s in t.stops}
    trip_by_id = {t.id: t for t in trips}
    orders = (
        db.query(Order)
        .filter(Order.workspace_id == ws.id, Order.service_date == plan.service_date, Order.status.in_(("planned", "deferred")))
        .all()
    )
    for o in orders:
        aud = f"outlet:{o.outlet_id}"
        label = "chilled order" if o.temp == "chilled" else "dry goods"
        if o.id in deferrals:
            d = deferrals[o.id]
            again = " It is the second run in a row, so you are first in line." if o.deferred_yesterday else ""
            post_notice(
                db,
                ws,
                aud,
                f"Your {label} moved to {plan.service_date + timedelta(days=1):%A}",
                f"{_store_reason(d.code)}.{again}",
                kind="ember",
                icon="cal",
                key=f"order:{o.id}",
                entity=f"order:{o.id}",
            )
        elif o.id in stops:
            s = stops[o.id]
            t = trip_by_id[s.trip_id]
            lo, hi = s.planned_arrival - timedelta(minutes=4), s.planned_arrival + timedelta(minutes=6 + round(s.late_prob * 38))
            ou = ref.outlets[o.outlet_id]
            post_notice(
                db,
                ws,
                aud,
                f"Your {label} for {day} is planned",
                f"{t.vehicle_id} with {ref.vehicles[t.vehicle_id].driver_name.split(' ')[0]}, arriving about {lo:%H:%M}–{hi:%H:%M}. "
                f"Your window is {ou.window_open}–{ou.window_close}.",
                kind="blue",
                icon="truck",
                key=f"order:{o.id}",
                entity=f"order:{o.id}",
            )
    record(
        db,
        ws,
        "plan.published",
        actor_name=actor,
        actor_role="dispatcher",
        entity=f"plan:{plan.id}",
        payload={"version": plan.version, "policy": plan.policy},
        topics=["plan"],
    )
    notify(db, ws.id, ["plan", "depot:Kandy", "depot:Peliyagoda", "vehicle:*", "outlet:*"])
    post_notice(
        db,
        ws,
        "dispatch",
        f"Plan v{plan.version} published · {POLICY_LABEL[plan.policy]}",
        "Docks, drivers and stores have it now.",
        kind="green",
        icon="check",
        key="plan",
    )
    return plan


def _store_reason(code: str) -> str:
    return {
        "Refrigerated space": "Refrigerated space ran out on today's run",
        "Fresh window": "No vehicle could reach you inside the morning window",
        "Too big": "The order is larger than any of our vehicles. Please split it into two orders",
        "Too heavy": "The order is heavier than any of our vehicles. Please split it",
        "Van access": "Your street needs a small van and none was free",
        "Fuel quota": "The vehicles that reach you are at their weekly fuel limit",
    }.get(code, f"Reason: {code}")


# ------------------------------------------------------------------------------------------------
# deferral lever
# ------------------------------------------------------------------------------------------------


def _summary(sol: Solution, built: Built) -> dict:
    ref = RefCache
    deferred = []
    for d in sol.deferred:
        o = built.orders[int(d.order_id)]
        deferred.append(
            {
                "order_id": o.id,
                "ref": o.ref,
                "outlet": o.outlet_id,
                "name": ref.outlets[o.outlet_id].name,
                "brand": o.brand,
                "temp": o.temp,
                "m3": round(o.volume_m3, 2),
                "streak": o.deferral_streak + 1,
                "repeat": o.deferred_yesterday,
                "days_since_served": o.days_since_served,
                "kind": d.kind,
                "code": d.code,
                "text": d.text,
            }
        )
    assign = {int(k): {"vehicle": v[0], "trip_no": v[1]} for k, v in sol.assignments.items()}
    return {"policy": sol.policy, "kpis": sol.kpis, "deferred": deferred, "assign": assign, "ms": sol.solve_ms}


def lever(db: Session, ws: Workspace, plan: Plan) -> dict:
    cache = plan.lever_cache or {}
    if cache.get("version") == plan.version and all(p in cache.get("policies", {}) for p in ("throughput", "balanced", "fairness")):
        return cache
    policies = {}
    built_cur = build_problem(db, ws, plan.policy)
    # the current plan's own numbers come from the database, not a re-solve
    cur_assign = {}
    for t in db.query(Trip).filter_by(plan_id=plan.id):
        for s in t.stops:
            cur_assign[s.order_id] = {"vehicle": t.vehicle_id, "trip_no": t.trip_no}
    cur_def = []
    ref = RefCache.load(db)
    for d in db.query(Deferral).filter_by(plan_id=plan.id):
        o = built_cur.orders.get(d.order_id) or db.get(Order, d.order_id)
        cur_def.append(
            {
                "order_id": o.id,
                "ref": o.ref,
                "outlet": o.outlet_id,
                "name": ref.outlets[o.outlet_id].name,
                "brand": o.brand,
                "temp": o.temp,
                "m3": round(o.volume_m3, 2),
                "streak": d.streak,
                "repeat": o.deferred_yesterday,
                "days_since_served": o.days_since_served,
                "kind": d.kind,
                "code": d.code,
                "text": d.text,
            }
        )
    policies[plan.policy] = {
        "policy": plan.policy,
        "kpis": plan.kpis,
        "deferred": cur_def,
        "assign": cur_assign,
        "ms": plan.solver.get("solve_ms"),
    }
    others = [p for p in ("throughput", "balanced", "fairness") if p != plan.policy]

    def run(p: str) -> dict:
        b = build_problem(db, ws, p)
        b.problem.time_limit_s = max(2.0, get_settings().solver_time_limit_s * 0.7)
        sol = engine_plan(b.problem, explain_budget_s=max(1.5, get_settings().explain_budget_s * 0.6))
        return _summary(sol, b)

    with ThreadPoolExecutor(max_workers=2) as ex:
        for p, res in zip(others, ex.map(run, others), strict=False):
            policies[p] = res
    # what changes relative to the current plan
    for res in policies.values():
        changes = []
        cur_served = set(cur_assign)
        new_served = {int(k) for k in res["assign"]}
        for oid in sorted(new_served - cur_served):
            o = db.get(Order, oid)
            a = res["assign"][oid] if oid in res["assign"] else res["assign"].get(str(oid))
            changes.append(
                {
                    "type": "served",
                    "order_id": oid,
                    "name": ref.outlets[o.outlet_id].name,
                    "temp": o.temp,
                    "repeat": o.deferred_yesterday,
                    "vehicle": a["vehicle"],
                    "trip_no": a["trip_no"],
                }
            )
        for oid in sorted(cur_served - new_served):
            o = db.get(Order, oid)
            text = next((d["text"] for d in res["deferred"] if d["order_id"] == oid), "")
            changes.append(
                {
                    "type": "deferred",
                    "order_id": oid,
                    "name": ref.outlets[o.outlet_id].name,
                    "temp": o.temp,
                    "repeat": o.deferred_yesterday,
                    "text": text,
                }
            )
        res["changes"] = changes
    cache = {"version": plan.version, "current": plan.policy, "policies": policies}
    plan.lever_cache = cache
    return cache


def apply_policy(db: Session, ws: Workspace, plan: Plan, policy: str, actor: str) -> Plan:
    if _any_started(db, plan):
        raise PlanningError("Loading has started, so the policy can no longer change today's plan.")
    was_published = plan.status == "published"
    before = {d.order_id for d in db.query(Deferral).filter_by(plan_id=plan.id)}
    sol, built = solve(db, ws, policy)
    new = persist(db, ws, sol, built, actor, note=f"Policy changed to {POLICY_LABEL[policy]}")
    after = {int(d.order_id) for d in sol.deferred}
    served_now = before - after
    deferred_now = after - before
    post_notice(
        db,
        ws,
        "dispatch",
        f"Plan v{new.version} · {POLICY_LABEL[policy]}",
        f"{len(served_now)} order{'s' if len(served_now) != 1 else ''} back on the plan, {len(deferred_now)} moved to tomorrow.",
        kind="blue",
        icon="layers",
        key="plan",
    )
    if was_published:
        publish(db, ws, new, actor)
    return new


# ------------------------------------------------------------------------------------------------
# manual changes
# ------------------------------------------------------------------------------------------------


def _vehicle_trips(db: Session, plan: Plan, vehicle_id: str, exclude_order: int | None = None) -> dict[int, list[Order]]:
    out: dict[int, list[Order]] = defaultdict(list)
    for t in db.query(Trip).filter_by(plan_id=plan.id, vehicle_id=vehicle_id):
        for s in t.stops:
            if s.order_id != exclude_order:
                out[t.trip_no].append(s.order)
    return out


def _eorder(o: Order) -> EOrder:
    ou = RefCache.outlets[o.outlet_id]
    return EOrder(
        str(o.id),
        o.outlet_id,
        o.brand,
        ou.district,
        ou.depot,
        ou.dock_type,
        ou.parking,
        o.temp,
        o.weight_kg,
        o.volume_m3,
        _hm(ou.window_open),
        _hm(ou.window_close),
        parse_window(ou.mall_window),
        o.deferred_yesterday,
        o.days_since_served,
        None,
        ou.name,
    )


def _evehicle(db: Session, ws: Workspace, vehicle_id: str) -> EVehicle:
    v = RefCache.vehicles[vehicle_id]
    vd = db.query(VehicleDay).filter_by(workspace_id=ws.id, date=ws.service_date, vehicle_id=vehicle_id).first()
    return EVehicle(
        v.id,
        v.type,
        v.temp,
        v.weight_cap_kg,
        v.volume_cap_m3,
        v.km_per_l,
        max(0.0, v.weekly_fuel_quota_l - (vd.fuel_used_l if vd else 0.0)),
        v.depot,
        vd is None or vd.status == "available",
    )


def _edistricts() -> dict[str, EDistrict]:
    return {d.name: EDistrict(d.name, d.depot, d.d2d_min, d.inter_min, d.d2d_km, d.inter_km) for d in RefCache.districts.values()}


def check_target(db: Session, ws: Workspace, plan: Plan, order: Order, vehicle_id: str, trip_no: int):
    RefCache.load(db)
    target = db.query(Trip).filter_by(plan_id=plan.id, vehicle_id=vehicle_id, trip_no=trip_no).first()
    if target and target.status in ("loaded", "out", "done"):
        return (
            None,
            target,
            f"{vehicle_id} has already {'left the depot' if target.status != 'loaded' else 'been sealed and released'}. Choose a trip that is still loading.",
        )
    vt = {k: [_eorder(o) for o in v] for k, v in _vehicle_trips(db, plan, vehicle_id, exclude_order=order.id).items()}
    res = check_move(
        _eorder(order),
        _evehicle(db, ws, vehicle_id),
        trip_no,
        vt,
        _edistricts(),
        dict(RefCache.allowance),
        trip_brand=target.brand if target else None,
        trip_district=target.district if target else None,
    )
    return res, target, res.message


def retimetable(db: Session, ws: Workspace, plan: Plan, vehicle_id: str) -> None:
    """Re-sequence and re-time a vehicle's trips that have not left yet (after a manual change)."""
    trips = db.query(Trip).filter_by(plan_id=plan.id, vehicle_id=vehicle_id).order_by(Trip.trip_no).all()
    if any(t.status in ("out", "done") for t in trips):
        return
    eorders: dict[str, EOrder] = {}
    plans = []
    for t in trips:
        if not t.stops:
            continue
        for s in t.stops:
            eorders[str(s.order_id)] = _eorder(s.order)
        d = RefCache.districts[t.district]
        plans.append(
            TripPlan(
                t.vehicle_id,
                t.trip_no,
                t.depot,
                t.brand,
                t.district,
                [str(s.order_id) for s in t.stops],
                trip_minutes(
                    _edistricts()[t.district],
                    t.brand,
                    [RefCache.outlets[s.order.outlet_id].dock_type for s in t.stops],
                    dict(RefCache.allowance),
                ),
                trip_km(_edistricts()[t.district], len(t.stops)),
                trip_litres(_edistricts()[t.district], len(t.stops), RefCache.vehicles[t.vehicle_id].km_per_l),
                sum(s.order.volume_m3 for s in t.stops),
                sum(s.order.weight_kg for s in t.stops),
            )
        )
        _ = d
    if not plans:
        return
    timed = timetable_vehicle(plans, eorders, _edistricts(), dict(RefCache.allowance))
    by_old = {t.trip_no: t for t in trips}
    for tp in timed:
        # timetable may renumber; map back by the set of orders
        t = next(t for t in trips if {str(s.order_id) for s in t.stops} == set(tp.order_ids))
        t.minutes, t.km, t.litres, t.volume_m3, t.weight_kg = tp.minutes, tp.km, tp.litres, tp.volume_m3, tp.weight_kg
        if t.status == "planned":
            t.planned_depart = at_minutes(t.service_date, tp.depart or 0)
            t.planned_return = at_minutes(t.service_date, tp.return_at or 0)
        seq_of = {oid: i for i, oid in enumerate(tp.order_ids)}
        times = {st.order_id: st for st in tp.stops}
        for s in t.stops:
            st = times[str(s.order_id)]
            s.seq = seq_of[str(s.order_id)]
            if s.status == "pending":
                s.planned_arrival = at_minutes(t.service_date, st.arrive)
                s.planned_start = at_minutes(t.service_date, st.start)
                s.planned_finish = at_minutes(t.service_date, st.finish)
                s.late_planned = st.late
        db.flush()
        predict_trip(db, t, {s.order_id: s.order for s in t.stops})
    _ = by_old


def move_order(db: Session, ws: Workspace, order_id: int, vehicle_id: str, trip_no: int, actor: str) -> dict:
    plan = current_plan(db, ws)
    if not plan:
        raise PlanningError("There is no plan yet.")
    order = db.get(Order, order_id)
    if not order or order.workspace_id != ws.id:
        raise PlanningError("Unknown order.")
    RefCache.load(db)
    stop = db.query(Stop).join(Trip).filter(Stop.order_id == order.id, Trip.plan_id == plan.id).first()
    if stop and stop.status != "pending":
        raise PlanningError(
            f"{RefCache.outlets[order.outlet_id].name} was already {stop.status} at {(stop.completed_at or stop.arrived_at):%H:%M}."
        )
    res, target, msg = check_target(db, ws, plan, order, vehicle_id, trip_no)
    if res is None or not res.ok:
        return {"ok": False, "message": msg}
    source = stop.trip if stop else None
    if source and source.signal == "dark" and source.status == "out":
        from ..models import PendingMove

        # one queued move per order: a second decision while the van is still dark replaces the first
        pm = db.query(PendingMove).filter_by(workspace_id=ws.id, order_id=order.id, status="queued").first()
        replaced = pm.to_vehicle_id if pm else None
        if pm is None:
            pm = PendingMove(workspace_id=ws.id, order_id=order.id, from_trip_id=source.id)
            db.add(pm)
        pm.to_vehicle_id, pm.to_trip_no, pm.created_by, pm.created_at = vehicle_id, trip_no, actor, now_virtual(ws)
        if replaced and replaced != vehicle_id:
            resolve_notices(db, ws, f"vehicle:{replaced}", f"move:{order.id}")
        record(
            db,
            ws,
            "move.queued",
            actor_name=actor,
            actor_role="dispatcher",
            entity=f"order:{order.id}",
            payload={"to": [vehicle_id, trip_no], "from": source.vehicle_id},
            topics=[f"vehicle:{vehicle_id}", f"vehicle:{source.vehicle_id}"],
        )
        name = RefCache.outlets[order.outlet_id].name
        post_notice(
            db,
            ws,
            "dispatch",
            f"Move queued: {name} → {vehicle_id}",
            f"{source.vehicle_id} has no signal. The move reaches it on its next sync; if it delivered first, the delivery wins.",
            kind="blue",
            icon="clock",
            key=f"move:{order.id}",
            actions=[["Show trip", f"locate:{source.code}"]],
        )
        post_notice(
            db,
            ws,
            f"vehicle:{vehicle_id}",
            f"New stop: {name}",
            f"Added to trip {trip_no} while {source.vehicle_id} is out of coverage. It will be confirmed when {source.vehicle_id} syncs.",
            kind="blue",
            icon="plus",
            key=f"move:{order.id}",
        )
        return {
            "ok": True,
            "queued": True,
            "message": f"{name} will move to {vehicle_id} trip {trip_no} when {source.vehicle_id} reconnects.",
        }
    return _apply_move(db, ws, plan, order, stop, target, vehicle_id, trip_no, actor, msg)


def _apply_move(db, ws, plan, order, stop, target, vehicle_id, trip_no, actor, msg) -> dict:
    ref = RefCache
    name = ref.outlets[order.outlet_id].name
    was_deferred = order.status == "deferred"
    source_vehicle = stop.trip.vehicle_id if stop else None
    if stop:
        if stop.trip.vehicle_id != vehicle_id or stop.trip.trip_no != trip_no:
            _dock_change_for_removed(db, ws, stop, f"It moved to {vehicle_id} trip {trip_no}.")
        db.delete(stop)
        db.flush()
    db.query(Deferral).filter_by(plan_id=plan.id, order_id=order.id).delete()
    if target is None:
        ou = ref.outlets[order.outlet_id]
        n_depot = db.query(Trip).filter_by(plan_id=plan.id, depot=ou.depot).count()
        target = Trip(
            workspace_id=ws.id,
            plan_id=plan.id,
            service_date=ws.service_date,
            code=f"{depot_code(ou.depot)}{n_depot + 1}",
            vehicle_id=vehicle_id,
            trip_no=trip_no,
            depot=ou.depot,
            brand=order.brand,
            district=ou.district,
            status="planned",
            planned_depart=at_minutes(ws.service_date, 300),
            controlled="human" if vehicle_id in driver_vehicles(db) else "sim",
            dock=1,
            bay=(n_depot % 5) + 1,
        )
        db.add(target)
        db.flush()
    t0 = target.planned_depart
    db.add(Stop(workspace_id=ws.id, trip_id=target.id, order_id=order.id, seq=99, planned_arrival=t0, planned_start=t0, planned_finish=t0))
    db.flush()
    db.refresh(target)
    order.status = "planned"
    retimetable(db, ws, plan, vehicle_id)
    if source_vehicle and source_vehicle != vehicle_id:
        retimetable(db, ws, plan, source_vehicle)
        for t in db.query(Trip).filter_by(plan_id=plan.id, vehicle_id=source_vehicle):
            if not t.stops:
                db.delete(t)
    plan.version += 1
    new_stop = db.query(Stop).filter_by(trip_id=target.id, order_id=order.id).first()
    record(
        db,
        ws,
        "order.moved",
        actor_name=actor,
        actor_role="dispatcher",
        entity=f"order:{order.id}",
        payload={"to": [vehicle_id, trip_no], "from": source_vehicle, "was_deferred": was_deferred},
        topics=["plan", f"vehicle:{vehicle_id}", f"outlet:{order.outlet_id}", f"depot:{target.depot}"]
        + ([f"vehicle:{source_vehicle}"] if source_vehicle else []),
    )
    post_notice(
        db,
        ws,
        "dispatch",
        f"{name} {'served' if was_deferred else 'moved'}",
        f"Now on {vehicle_id} trip {trip_no}, arriving ~{new_stop.planned_arrival:%H:%M}. Checked against capacity, temperature, access, time and fuel.",
        kind="blue",
        icon="arrowR",
        key=f"move:{order.id}",
    )
    post_notice(
        db,
        ws,
        f"depot:{target.depot}",
        f"Stage {name} for {vehicle_id} trip {trip_no}",
        f"{'Chilled' if order.temp == 'chilled' else 'Ambient'} · {len(order.lines)} lines, {order.units} cases, {order.volume_m3:.1f} m³. "
        f"Load it before {target.planned_depart:%H:%M}."
        + (f" It came off {source_vehicle}." if source_vehicle and source_vehicle != vehicle_id else "")
        + (" It was deferred yesterday, so it has to go today." if order.deferred_yesterday else ""),
        kind="blue",
        icon="plus",
        key=f"new:{order.id}",
        entity=f"trip:{target.id}",
    )
    label = "chilled order" if order.temp == "chilled" else "order"
    if True:
        post_notice(
            db,
            ws,
            f"outlet:{order.outlet_id}",
            f"Your {label} is {'back on today' if was_deferred else 'on a different van'}",
            f"It will come on {vehicle_id}, arriving about {new_stop.planned_arrival:%H:%M}.",
            kind="green" if was_deferred else "blue",
            icon="truck",
            key=f"order:{order.id}",
        )
    return {"ok": True, "message": f"{name} moved to {vehicle_id} trip {trip_no}. {msg}"}


def _dock_change_for_removed(db: Session, ws: Workspace, stop: Stop, why: str) -> None:
    """A stop is leaving a trip the dock may already be loading. Lines go back to "to load" (they
    will be loaded again on their new vehicle) and the dock is told what to take off."""
    trip = stop.trip
    order = stop.order
    name = RefCache.outlets[order.outlet_id].name
    loaded = [ln for ln in order.lines if ln.load_state != "todo"]
    for ln in order.lines:
        ln.load_state, ln.loaded_qty, ln.loaded_by, ln.loaded_at = "todo", None, None, None
    if trip.status not in ("planned", "loading", "loaded"):
        return
    if loaded:
        sealed = trip.status == "loaded"
        post_notice(
            db,
            ws,
            f"depot:{trip.depot}",
            f"{'Unseal and take' if sealed else 'Take'} off {name} from {trip.vehicle_id}",
            f"{len(loaded)} of {len(order.lines)} lines ({order.units} cases, {order.volume_m3:.1f} m³) are already on the truck. {why}",
            kind="ember",
            icon="minus",
            key=f"take:{order.id}",
            entity=f"trip:{trip.id}",
        )
    else:
        post_notice(
            db,
            ws,
            f"depot:{trip.depot}",
            f"Don't load {name} on {trip.vehicle_id}",
            why,
            kind="amber",
            icon="layers",
            key=f"chg:{order.id}",
            entity=f"trip:{trip.id}",
        )


def defer_order(db: Session, ws: Workspace, order_id: int, reason: str, actor: str) -> dict:
    plan = current_plan(db, ws)
    order = db.get(Order, order_id)
    if not plan or not order or order.workspace_id != ws.id:
        raise PlanningError("Unknown order.")
    RefCache.load(db)
    stop = db.query(Stop).join(Trip).filter(Stop.order_id == order.id, Trip.plan_id == plan.id).first()
    if stop and stop.status != "pending":
        raise PlanningError("It has already been delivered.")
    if stop and stop.trip.status in ("loaded", "out", "done"):
        raise PlanningError(f"{stop.trip.vehicle_id} has already been released; ask the driver to return it instead.")
    vehicle = stop.trip.vehicle_id if stop else None
    if stop:
        _dock_change_for_removed(db, ws, stop, f"Moved to tomorrow by {actor}: {reason.lower()}.")
        db.delete(stop)
        db.flush()
    db.query(Deferral).filter_by(plan_id=plan.id, order_id=order.id).delete()
    order.status = "deferred"
    now = now_virtual(ws)
    db.add(
        Deferral(
            workspace_id=ws.id,
            plan_id=plan.id,
            order_id=order.id,
            kind="manual",
            code=reason,
            text=f"Deferred by {actor} at {now:%H:%M}: {reason.lower()}.",
            streak=order.deferral_streak + 1,
            created_at=now,
            decided_by=actor,
            next_date=ws.service_date + timedelta(days=1),
        )
    )
    if vehicle:
        retimetable(db, ws, plan, vehicle)
    plan.version += 1
    name = RefCache.outlets[order.outlet_id].name
    record(
        db,
        ws,
        "order.deferred",
        actor_name=actor,
        actor_role="dispatcher",
        entity=f"order:{order.id}",
        payload={"reason": reason},
        topics=["plan", f"outlet:{order.outlet_id}"] + ([f"vehicle:{vehicle}"] if vehicle else []),
    )
    post_notice(
        db,
        ws,
        "dispatch",
        f"{name} deferred",
        f"Reason: {reason}. Streak {order.deferral_streak + 1}. Store notified.",
        kind="ember",
        icon="cal",
    )
    post_notice(
        db,
        ws,
        f"outlet:{order.outlet_id}",
        f"An order was moved to {ws.service_date + timedelta(days=1):%A}",
        f"Reason: {reason}. It arrives on the next run, first in line.",
        kind="ember",
        icon="cal",
        key=f"order:{order.id}",
    )
    return {"ok": True, "message": f"{name} deferred to {ws.service_date + timedelta(days=1):%a %-d %b}. The store has been told."}


def move_options(db: Session, ws: Workspace, order_id: int) -> list[dict]:
    """Where could this order go right now? Existing trips and empty trip slots, with the reason
    each one is or is not possible, and the predicted arrival against the window."""
    plan = current_plan(db, ws)
    order = db.get(Order, order_id)
    if not plan or not order:
        return []
    RefCache.load(db)
    ou = RefCache.outlets[order.outlet_id]
    out = []
    current = db.query(Stop).join(Trip).filter(Stop.order_id == order.id, Trip.plan_id == plan.id).first()
    for v in RefCache.vehicles.values():
        if v.depot != ou.depot:
            continue
        if order.temp == "chilled" and v.temp != "reefer":
            continue
        trips = {t.trip_no: t for t in db.query(Trip).filter_by(plan_id=plan.id, vehicle_id=v.id)}
        for tno in (1, 2):
            t = trips.get(tno)
            if current and t and current.trip_id == t.id:
                continue
            if t and (t.brand != order.brand or t.district != ou.district):
                continue
            if not t and len(trips) >= 2:
                continue
            res, target, msg = check_target(db, ws, plan, order, v.id, tno)
            out.append(
                {
                    "vehicle": v.id,
                    "trip_no": tno,
                    "existing": bool(t),
                    "ok": bool(res and res.ok),
                    "message": msg,
                    "departs": t.planned_depart.isoformat() if t else None,
                    "driver": v.driver_name,
                }
            )
    out.sort(key=lambda r: (not r["ok"], not r["existing"], r["vehicle"]))
    return out[:12]
