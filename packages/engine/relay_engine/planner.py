"""Public entry point: plan a day.

    solution = plan(problem)            # stage 1 per depot in parallel, stage 2, explanations, KPIs

The API calls this from a worker thread; the Task 2B CLI calls it directly.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from .core import Problem, Solution, TripPlan
from .explain import explain
from .model import DepotResult, objective_of, solve_depot
from .standards import trip_km, trip_litres, trip_minutes
from .timetable import timetable_vehicle


def _workers_per_depot(n_depots: int, requested: int) -> int:
    cpus = os.cpu_count() or 2
    return max(2, min(requested, (cpus * 2) // max(1, n_depots)))


def build_trips(problem: Problem, assignments: dict[str, tuple[str, int]], service: dict[str, float] | None = None) -> list[TripPlan]:
    orders = {o.id: o for o in problem.orders}
    vehicles = {v.id: v for v in problem.vehicles}
    groups: dict[tuple[str, int], list[str]] = defaultdict(list)
    for oid, key in assignments.items():
        groups[key].append(oid)
    per_vehicle: dict[str, list[TripPlan]] = defaultdict(list)
    for (vid, tno), oids in groups.items():
        os_ = [orders[i] for i in oids]
        first = os_[0]
        d = problem.districts[first.district]
        v = vehicles[vid]
        per_vehicle[vid].append(
            TripPlan(
                vehicle_id=vid,
                trip_no=tno,
                depot=first.depot,
                brand=first.brand,
                district=first.district,
                order_ids=oids,
                minutes=trip_minutes(d, first.brand, [o.dock_type for o in os_], problem.allowance),
                km=round(trip_km(d, len(os_)), 1),
                litres=round(trip_litres(d, len(os_), v.km_per_l), 1),
                volume_m3=round(sum(o.volume_m3 for o in os_), 3),
                weight_kg=round(sum(o.weight_kg for o in os_), 1),
            )
        )
    trips: list[TripPlan] = []
    for ts in per_vehicle.values():
        trips.extend(timetable_vehicle(ts, orders, problem.districts, problem.allowance, service))
    trips.sort(key=lambda t: (t.depot, t.depart or 0, t.vehicle_id, t.trip_no))
    return trips


def kpis(problem: Problem, assignments: dict[str, tuple[str, int]], trips: list[TripPlan]) -> dict:
    out: dict = {"by_depot": {}}
    for depot in sorted({o.depot for o in problem.orders}) + ["all"]:
        os_ = [o for o in problem.orders if depot == "all" or o.depot == depot]
        served = [o for o in os_ if o.id in assignments]
        chilled = [o for o in os_ if o.temp == "chilled"]
        ts = [t for t in trips if depot == "all" or t.depot == depot]
        k = {
            "orders": len(os_),
            "served": len(served),
            "deferred": len(os_) - len(served),
            "volume_m3": round(sum(o.volume_m3 for o in os_), 2),
            "volume_served_m3": round(sum(o.volume_m3 for o in served), 2),
            "chilled_m3": round(sum(o.volume_m3 for o in chilled), 2),
            "chilled_served_m3": round(sum(o.volume_m3 for o in chilled if o.id in assignments), 2),
            "repeat_skips": sum(1 for o in os_ if o.deferred_yesterday and o.id not in assignments),
            "repeats_total": sum(1 for o in os_ if o.deferred_yesterday),
            "trips": len(ts),
            "vehicles": len({t.vehicle_id for t in ts}),
            "km": round(sum(t.km for t in ts), 1),
            "litres": round(sum(t.litres for t in ts), 1),
            "late_stops": sum(1 for t in ts for s in t.stops if s.late),
        }
        if depot == "all":
            out.update(k)
        else:
            out["by_depot"][depot] = k
    return out


def plan(
    problem: Problem,
    *,
    explain_budget_s: float = 6.0,
    service: dict[str, float] | None = None,
    hints: dict[str, tuple[str, int]] | None = None,
) -> Solution:
    t0 = time.perf_counter()
    depots = sorted({o.depot for o in problem.orders})
    workers = _workers_per_depot(len(depots), problem.workers)
    p = replace(problem, workers=workers)
    results: dict[str, DepotResult] = {}
    if len(depots) > 1 and not problem.deterministic:
        with ThreadPoolExecutor(max_workers=len(depots)) as ex:
            futs = {d: ex.submit(solve_depot, p, d, hints) for d in depots}
            results = {d: f.result() for d, f in futs.items()}
    else:
        results = {d: solve_depot(p, d, hints) for d in depots}

    assignments: dict[str, tuple[str, int]] = {}
    for r in results.values():
        assignments.update(r.assignments)
    trips = build_trips(problem, assignments, service)
    # stage 2 renumbered trips; reflect that in the assignments
    assignments = {oid: (t.vehicle_id, t.trip_no) for t in trips for oid in t.order_ids}
    for r in results.values():
        r.assignments = {oid: assignments[oid] for oid in r.assignments}

    deferred_orders = [o for o in problem.orders if o.id not in assignments]
    reasons = explain(p, results, deferred_orders, budget_s=explain_budget_s) if deferred_orders else {}

    # Repair: a counterfactual that serves an order without displacing anything proves the
    # time-limited search left value on the table; serve those orders and re-explain the rest.
    free = [oid for oid, r in reasons.items() if r.kind == "choice" and r.cost.get("orders") == 0 and r.cost.get("displaced") == []]
    if free:
        improved = False
        for depot in depots:
            want = {oid for oid in free if next(o for o in problem.orders if o.id == oid).depot == depot}
            if not want:
                continue
            p2 = replace(p, force_served=want, time_limit_s=max(1.5, problem.time_limit_s * 0.5))
            r2 = solve_depot(p2, depot, hint=results[depot].assignments)
            if r2.status in ("OPTIMAL", "FEASIBLE") and objective_of(problem, r2.assignments) > objective_of(
                problem, results[depot].assignments
            ):
                results[depot] = r2
                improved = True
        if improved:
            assignments = {}
            for r in results.values():
                assignments.update(r.assignments)
            trips = build_trips(problem, assignments, service)
            assignments = {oid: (t.vehicle_id, t.trip_no) for t in trips for oid in t.order_ids}
            for r in results.values():
                r.assignments = {oid: assignments[oid] for oid in r.assignments if oid in assignments}
            deferred_orders = [o for o in problem.orders if o.id not in assignments]
            reasons = explain(p, results, deferred_orders, budget_s=explain_budget_s * 0.6) if deferred_orders else {}
    statuses = {r.status for r in results.values()}
    status = "OPTIMAL" if statuses == {"OPTIMAL"} else ("FEASIBLE" if statuses <= {"OPTIMAL", "FEASIBLE"} else "/".join(sorted(statuses)))
    sol = Solution(
        status=status,
        policy=problem.policy,
        objective=sum(r.objective for r in results.values()),
        bound=sum(r.bound for r in results.values()),
        solve_ms=int((time.perf_counter() - t0) * 1000),
        trips=trips,
        assignments=assignments,
        deferred=[reasons[o.id] for o in deferred_orders if o.id in reasons],
        kpis=kpis(problem, assignments, trips),
    )
    sol.kpis["depot_status"] = {d: {"status": r.status, "ms": r.solve_ms, "vars": r.n_vars, "gap": _gap(r)} for d, r in results.items()}
    return sol


def _gap(r: DepotResult) -> float:
    if not r.objective:
        return 0.0
    return round(abs(r.bound - r.objective) / max(1.0, abs(r.objective)), 4)
