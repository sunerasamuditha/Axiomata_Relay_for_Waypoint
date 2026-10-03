"""Explain every deferral as *unavoidable* or a *choice*, with what it costs.

1. Static checks catch the obvious unavoidable cases without solving anything:
   no free refrigerated vehicle, no free van for a van-only outlet, an order bigger or heavier
   than any vehicle that may carry it, a single stop that already breaks the time window, or no
   compatible vehicle with fuel left.
2. Everything else gets a counterfactual: re-solve the order's depot with that order *forced*
   onto the plan (warm-started from the published solution).
     - infeasible  -> unavoidable: no allocation can serve it today
     - feasible    -> a choice: serving it would push these other orders (named, with volume
                      and repeat-skip count) to tomorrow under the same policy
     - timed out   -> reported as a choice without a priced cost (honest about what we know)
"""

from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from .core import Deferral, Order, Problem
from .model import DepotResult, _solve, fits_alone, solve_depot
from .policies import POLICY_LABEL
from .standards import budget_for


def _names(orders: list[Order], limit: int = 3) -> str:
    labels = [o.label or o.outlet_id for o in orders]
    if len(labels) <= limit:
        return ", ".join(labels)
    return ", ".join(labels[:limit]) + f" and {len(labels) - limit} more"


def resource_code(o: Order) -> str:
    if o.temp == "chilled":
        return "Refrigerated space"
    if o.parking == "van_only":
        return "Van access"
    if o.brand == "Fresh":
        return "Fresh window"
    return "Capacity"


def static_reason(o: Order, problem: Problem) -> Deferral | None:
    depot_vs = [v for v in problem.vehicles if v.depot == o.depot]
    free = [v for v in depot_vs if v.available]
    name = o.label or o.outlet_id
    if o.temp == "chilled":
        cold = [v for v in depot_vs if v.temp == "reefer"]
        cold_free = [v for v in cold if v.available]
        if not cold_free:
            return Deferral(
                o.id, "unavoidable", "Refrigerated space", f"All {len(cold)} refrigerated vehicles at {o.depot} are in the workshop today."
            )
    if o.parking == "van_only":
        vans = [v for v in depot_vs if v.type == "van" and (o.temp != "chilled" or v.temp == "reefer")]
        if not [v for v in vans if v.available]:
            return Deferral(o.id, "unavoidable", "Van access", f"{name} is van-only and no suitable van is free at {o.depot} today.")
    kind = [v for v in free if (o.temp != "chilled" or v.temp == "reefer") and (o.parking != "van_only" or v.type == "van")]
    if kind:
        max_vol = max(v.volume_cap_m3 for v in kind)
        max_kg = max(v.weight_cap_kg for v in kind)
        if o.volume_m3 > max_vol + 1e-9:
            return Deferral(
                o.id,
                "unavoidable",
                "Too big",
                f"{o.volume_m3:.1f} m³ is larger than any vehicle that can carry it (largest {max_vol:.0f} m³). Ask the store to split it into two orders.",
                {"largest_m3": max_vol},
            )
        if o.weight_kg > max_kg + 1e-9:
            return Deferral(
                o.id,
                "unavoidable",
                "Too heavy",
                f"{o.weight_kg:,.0f} kg is heavier than any vehicle that can carry it (largest {max_kg:,.0f} kg). Ask the store to split it.",
                {"largest_kg": max_kg},
            )
    d = problem.districts[o.district]
    alone = d.d2d_min + problem.allowance[(o.brand, o.dock_type)]
    budget = budget_for(o.brand, problem.budget_fresh, problem.budget_day)
    if alone > budget:
        return Deferral(
            o.id,
            "unavoidable",
            "Fresh window" if o.is_fresh else "Daytime window",
            f"Even as a single stop the trip takes {alone} min, more than the {budget}-minute window.",
        )
    if kind and not [v for v in kind if fits_alone(o, v, problem)]:
        litres = 2 * d.d2d_km
        return Deferral(
            o.id,
            "unavoidable",
            "Fuel quota",
            f"Every vehicle that could reach {o.district} is near its weekly fuel quota (the round trip is {litres:.0f} km).",
        )
    return None


def _binding_text(o: Order, problem: Problem) -> str:
    depot_vs = [v for v in problem.vehicles if v.depot == o.depot]
    if o.temp == "chilled":
        cold = [v for v in depot_vs if v.temp == "reefer"]
        out = [v for v in cold if not v.available]
        extra = f" {len(out)} of {len(cold)} cold vehicles at {o.depot} are in the workshop." if out else ""
        return f"Every free refrigerated vehicle at {o.depot} is full or out of pre-dawn time.{extra}"
    if o.parking == "van_only":
        return f"The free vans at {o.depot} are full or out of time."
    d = problem.districts[o.district]
    if o.is_fresh:
        return f"No vehicle that can reach {o.district} has {d.inter_min + problem.allowance[(o.brand, o.dock_type)]} pre-dawn minutes left, and a new trip needs {d.d2d_min + problem.allowance[(o.brand, o.dock_type)]}."
    return f"No vehicle at {o.depot} has room or daytime left for {o.district}."


def counterfactual(problem: Problem, o: Order, base: DepotResult, time_limit_s: float, workers: int = 2) -> Deferral:
    p2 = replace(problem, force_served={o.id}, time_limit_s=time_limit_s, workers=workers, pins=list(problem.pins))
    t0 = time.perf_counter()
    if o.temp == "chilled":
        # The cold chain is where chilled orders compete: re-solve chilled orders on refrigerated
        # vehicles only (exact and fast). Ambient goods sharing a reefer can always move to the
        # dry fleet, so they are not counted as displaced.
        cold = [x for x in problem.orders if x.depot == o.depot and x.temp == "chilled"]
        reefers = [v for v in problem.vehicles if v.depot == o.depot and v.available and v.temp == "reefer"]
        cold_ids = {x.id for x in cold}
        hint = {k: v for k, v in base.assignments.items() if k in cold_ids}
        r = _solve(p2, o.depot, cold, reefers, hint, time_limit_s, t0)
        base_served = [oid for oid in base.assignments if oid in cold_ids]
    else:
        r = solve_depot(p2, o.depot, hint=base.assignments)
        base_served = list(base.assignments)
    code = resource_code(o)
    if r.status == "INFEASIBLE":
        return Deferral(o.id, "unavoidable", code, _binding_text(o, problem) + " No allocation can serve it today.")
    if r.status in ("OPTIMAL", "FEASIBLE"):
        by_id = {x.id: x for x in problem.orders}
        displaced = [by_id[oid] for oid in base_served if oid not in r.assignments and oid in by_id]
        if displaced:
            vol = sum(x.volume_m3 for x in displaced)
            reps = sum(1 for x in displaced if x.deferred_yesterday)
            rep_txt = f", {reps} of them already skipped yesterday" if reps else ""
            tail = ""
            if len(displaced) == 1 and not displaced[0].deferred_yesterday:
                tail = " It was served yesterday."
            text = (
                f"Serving it would move {len(displaced)} order{'s' if len(displaced) != 1 else ''} "
                f"({vol:.1f} m³{rep_txt}) to tomorrow: {_names(displaced)}.{tail} "
                f"{POLICY_LABEL.get(problem.policy, problem.policy)} keeps them instead."
            )
            return Deferral(
                o.id,
                "choice",
                code,
                text.strip(),
                {"displaced": [x.id for x in displaced], "volume_m3": round(vol, 2), "orders": len(displaced), "repeats": reps},
            )
        return Deferral(
            o.id,
            "choice",
            code,
            f"It fits if a vehicle adds a trip; {POLICY_LABEL.get(problem.policy, problem.policy)} judged the extra trip not worth it.",
            {"displaced": [], "orders": 0},
        )
    return Deferral(
        o.id,
        "choice",
        code,
        f"{_binding_text(o, problem)} The planner kept higher-priority orders under {POLICY_LABEL.get(problem.policy, problem.policy)}.",
        {"priced": False},
    )


def explain(
    problem: Problem,
    base: dict[str, DepotResult],
    deferred: list[Order],
    budget_s: float = 6.0,
    parallel: int = 3,
) -> dict[str, Deferral]:
    out: dict[str, Deferral] = {}
    todo: list[Order] = []
    for o in deferred:
        s = static_reason(o, problem)
        if s:
            out[o.id] = s
        else:
            todo.append(o)
    if not todo:
        return out
    if budget_s <= 0:
        for o in todo:
            out[o.id] = Deferral(o.id, "choice", resource_code(o), _binding_text(o, problem), {"priced": False})
        return out
    rounds = math.ceil(len(todo) / parallel)
    per = max(0.6, min(3.0, budget_s / rounds))
    with ThreadPoolExecutor(max_workers=parallel) as ex:
        futs = {o.id: ex.submit(counterfactual, problem, o, base[o.depot], per) for o in todo}
        for oid, f in futs.items():
            out[oid] = f.result()
    return out
