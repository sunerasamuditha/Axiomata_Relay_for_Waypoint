"""Stage 1: assignment of orders to vehicle trips with CP-SAT (OR-Tools).

One model per depot (depots never share vehicles, so they are independent problems and solve in
parallel).

Decision variables
  x[o, v, t] = 1 if order o rides on vehicle v, trip slot t (t in {1, 2})
  y[v, t, c] = 1 if trip slot (v, t) serves combo c = (brand, district)
  u[v, t]    = 1 if trip slot (v, t) is used  (= sum_c y[v, t, c])

Hard constraints (the booklet's operating rules)
  * each order served at most once (whole orders, never split)
  * a trip serves at most one (brand, district) combo, and an order needs its combo's trip
  * refrigeration, van-only access, home depot, workshop vehicles: pruned from x up front
  * volume and weight per trip
  * trip time per the published standard, summed per vehicle:
        Fresh trips <= 270 min (pre-dawn), Style + Tech trips <= 480 min (daytime)
    using  minutes(trip) = (d2d - inter) * u + sum over its orders of (inter + allowance)
    which equals d2d + (n - 1) * inter + sum(allowance) exactly, as in check_allocation.py
  * weekly fuel: kilometres of all trips (return leg included) <= fuel left * km_per_l
  * at most two trips per vehicle

Speed-ups (the dispatcher waits for this, on two vCPUs)
  * symmetry breaking: interchangeable vehicles (same type, temperature, capacities, km/l and
    ample fuel) are used in a fixed order, and a vehicle's slot 2 is only used after slot 1
  * a greedy first-fit plan seeds the search as a hint
  * continuity (keeping an outlet on its usual vehicle) is restored afterwards by relabelling
    interchangeable vehicles, which changes nothing else about the plan

Objective (lexicographic tiers folded into one weighted sum; see policies.py)
  maximise   sum(score_o * served_o)
  minimise   trips used, kilometres
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from dataclasses import dataclass, field, replace

from ortools.sat.python import cp_model

from .core import Order, Pin, Problem, Vehicle
from .policies import CONTINUITY_BONUS, KM_COST, SECOND_FRESH_TRIP_COST, TRIP_COST, order_score
from .standards import budget_for

VOL_SCALE = 1000  # m3 -> integer litres
WEIGHT_SCALE = 10  # kg -> integer tenths of a kg
KM_SCALE = 10  # km -> integer tenths of a km (fuel constraint)


def _up(x: float, scale: int) -> int:
    """Demand in integer units, rounded up: the model may never under-count what a truck carries
    (rounding to the nearest unit let three 346.6 kg orders "fit" a 1,040 kg van)."""
    return math.ceil(x * scale - 1e-9)


def _down(x: float, scale: int) -> int:
    """Capacity in integer units, rounded down."""
    return math.floor(x * scale + 1e-9)


@dataclass
class DepotResult:
    depot: str
    status: str
    objective: float
    bound: float
    assignments: dict[str, tuple[str, int]]
    solve_ms: int
    n_vars: int = 0
    notes: list[str] = field(default_factory=list)


def fits_alone(o: Order, v: Vehicle, problem: Problem) -> bool:
    """Could this order ride on this vehicle at all (ignoring everything else on board)?"""
    if not v.available or v.depot != o.depot:
        return False
    if o.temp == "chilled" and v.temp != "reefer":
        return False
    if o.parking == "van_only" and v.type != "van":
        return False
    if o.volume_m3 > v.volume_cap_m3 + 1e-9 or o.weight_kg > v.weight_cap_kg + 1e-9:
        return False
    d = problem.districts[o.district]
    minutes = d.d2d_min + problem.allowance[(o.brand, o.dock_type)]
    if minutes > budget_for(o.brand, problem.budget_fresh, problem.budget_day):
        return False
    return not (v.km_per_l > 0 and 2 * d.d2d_km / v.km_per_l > v.fuel_left_l + 1e-09)


def _max_day_km(problem: Problem, depot: str) -> float:
    ds = [d for d in problem.districts.values() if d.depot == depot]
    if not ds:
        return 0.0
    worst = max(2 * d.d2d_km + 8 * d.inter_km for d in ds)
    return problem.max_trips * worst


def vehicle_class(v: Vehicle, problem: Problem) -> tuple:
    """Vehicles with the same class are interchangeable for every constraint in the model."""
    ample = v.km_per_l <= 0 or v.fuel_left_l * v.km_per_l >= _max_day_km(problem, v.depot)
    fuel_key = "ample" if ample else round(v.fuel_left_l, 1)
    return (v.type, v.temp, round(v.weight_cap_kg), round(v.volume_cap_m3, 2), round(v.km_per_l, 2), fuel_key)


# ---------------------------------------------------------------------------------------------
# greedy warm start
# ---------------------------------------------------------------------------------------------


def greedy(
    problem: Problem,
    depot: str,
    comp: dict[str, list[Vehicle]],
    fixed: dict[str, tuple[str, int]] | None = None,
) -> dict[str, tuple[str, int]]:
    """First-fit (best-fit on volume) by descending priority. `fixed` assignments are loaded first."""
    fixed = fixed or {}
    by_id = {o.id: o for o in problem.orders}
    # most constrained first (van-only, chilled: few vehicles can take them), then by priority
    orders = sorted(
        (o for o in problem.orders if o.depot == depot and comp.get(o.id) and o.id not in fixed),
        key=lambda o: (0 if len(comp[o.id]) <= 4 else 1, -order_score(o, problem.policy), -o.volume_m3),
    )
    trips: dict[str, list[dict]] = defaultdict(list)
    used_min: dict[tuple[str, bool], int] = defaultdict(int)
    used_km: dict[str, float] = defaultdict(float)
    out: dict[str, tuple[str, int]] = {}
    for oid, (vid, t) in sorted(fixed.items(), key=lambda kv: kv[1][1]):
        o = by_id[oid]
        d = problem.districts[o.district]
        while len(trips[vid]) < t:
            trips[vid].append({"combo": None, "vol": 0.0, "kg": 0.0, "n": 0})
        tr = trips[vid][t - 1]
        if tr["combo"] is None:
            tr["combo"] = o.combo
            used_min[(vid, o.is_fresh)] += d.d2d_min - d.inter_min
            used_km[vid] += 2 * d.d2d_km - d.inter_km
        tr["vol"] += o.volume_m3
        tr["kg"] += o.weight_kg
        tr["n"] += 1
        used_min[(vid, o.is_fresh)] += d.inter_min + problem.allowance[(o.brand, o.dock_type)]
        used_km[vid] += d.inter_km
    for o in orders:
        d = problem.districts[o.district]
        allow = problem.allowance[(o.brand, o.dock_type)]
        budget = budget_for(o.brand, problem.budget_fresh, problem.budget_day)
        best = None
        # continuity first: the outlet's usual vehicle, if it can take the order
        cands = sorted(comp[o.id], key=lambda v: 0 if v.id == o.usual_vehicle else 1)
        usual = next((v for v in cands if v.id == o.usual_vehicle), None)
        if usual is not None:
            for i, t in enumerate(trips[usual.id]):
                if (
                    t["combo"] == o.combo
                    and t["vol"] + o.volume_m3 <= usual.volume_cap_m3 + 1e-9
                    and t["kg"] + o.weight_kg <= usual.weight_cap_kg + 1e-9
                    and used_min[(usual.id, o.is_fresh)] + d.inter_min + allow <= budget
                    and (not usual.km_per_l or (used_km[usual.id] + d.inter_km) / usual.km_per_l <= usual.fuel_left_l)
                ):
                    best = (-1.0, usual, i)
                    break
            if (
                best is None
                and len(trips[usual.id]) < problem.max_trips
                and not any(t["combo"] == o.combo for t in trips[usual.id])
                and used_min[(usual.id, o.is_fresh)] + d.d2d_min + allow <= budget
                and (not usual.km_per_l or (used_km[usual.id] + 2 * d.d2d_km) / usual.km_per_l <= usual.fuel_left_l)
            ):
                trips[usual.id].append({"combo": o.combo, "vol": 0.0, "kg": 0.0, "n": 0})
                used_min[(usual.id, o.is_fresh)] += d.d2d_min - d.inter_min
                used_km[usual.id] += 2 * d.d2d_km - d.inter_km
                best = (-1.0, usual, len(trips[usual.id]) - 1)
        for v in comp[o.id] if best is None else []:
            for i, t in enumerate(trips[v.id]):
                if t["combo"] != o.combo:
                    continue
                if t["vol"] + o.volume_m3 > v.volume_cap_m3 + 1e-9 or t["kg"] + o.weight_kg > v.weight_cap_kg + 1e-9:
                    continue
                if used_min[(v.id, o.is_fresh)] + d.inter_min + allow > budget:
                    continue
                if v.km_per_l and (used_km[v.id] + d.inter_km) / v.km_per_l > v.fuel_left_l:
                    continue
                slack = v.volume_cap_m3 - t["vol"] - o.volume_m3
                if best is None or slack < best[0]:
                    best = (slack, v, i)
        if best is None:
            # open a new trip: pick the smallest vehicle that can take the rest of this combo's
            # demand (fewer, fuller trips), else the largest one available
            need = sum(x.volume_m3 for x in orders if x.combo == o.combo and x.id not in out) + 1e-9

            def rank(v: Vehicle, need: float = need) -> tuple:
                fits_all = v.volume_cap_m3 >= need
                return (0 if fits_all else 1, v.volume_cap_m3 if fits_all else -v.volume_cap_m3, -len(trips[v.id]), v.id)

            for v in sorted(comp[o.id], key=rank):
                if len(trips[v.id]) >= problem.max_trips:
                    continue
                if used_min[(v.id, o.is_fresh)] + d.d2d_min + allow > budget:
                    continue
                if v.km_per_l and (used_km[v.id] + 2 * d.d2d_km) / v.km_per_l > v.fuel_left_l:
                    continue
                trips[v.id].append({"combo": o.combo, "vol": 0.0, "kg": 0.0, "n": 0})
                used_min[(v.id, o.is_fresh)] += d.d2d_min - d.inter_min
                used_km[v.id] += 2 * d.d2d_km - d.inter_km
                best = (0.0, v, len(trips[v.id]) - 1)
                break
        if best is None:
            continue
        _, v, i = best
        t = trips[v.id][i]
        t["vol"] += o.volume_m3
        t["kg"] += o.weight_kg
        t["n"] += 1
        used_min[(v.id, o.is_fresh)] += d.inter_min + allow
        used_km[v.id] += d.inter_km
        out[o.id] = (v.id, i + 1)
    return out


# ---------------------------------------------------------------------------------------------
# continuity: relabel interchangeable vehicles so outlets keep their usual vehicle
# ---------------------------------------------------------------------------------------------


def relabel_for_continuity(
    problem: Problem, depot: str, assignments: dict[str, tuple[str, int]], pinned_vids: set[str]
) -> dict[str, tuple[str, int]]:
    orders = {o.id: o for o in problem.orders if o.depot == depot}
    vehicles = [v for v in problem.vehicles if v.depot == depot and v.available and v.id not in pinned_vids]
    classes: dict[tuple, list[Vehicle]] = defaultdict(list)
    for v in vehicles:
        classes[vehicle_class(v, problem)].append(v)
    schedule: dict[str, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    for oid, (vid, t) in assignments.items():
        schedule[vid][t].append(oid)
    mapping: dict[str, str] = {}
    for members in classes.values():
        if len(members) < 2:
            continue
        ids = [v.id for v in members]
        # score[(schedule_owner, target_vehicle)] = number of orders whose usual vehicle is target
        free = set(ids)
        owners = [vid for vid in ids if schedule.get(vid)]
        pairs = []
        for owner in owners:
            oids = [oid for t in schedule[owner].values() for oid in t]
            for target in ids:
                s = sum(1 for oid in oids if orders[oid].usual_vehicle == target)
                pairs.append((s, owner, target))
        pairs.sort(key=lambda p: (-p[0], p[1], p[2]))
        done_owner: set[str] = set()
        for _score, owner, target in pairs:
            if owner in done_owner or target not in free:
                continue
            mapping[owner] = target
            done_owner.add(owner)
            free.discard(target)
    if mapping:
        assignments = {oid: (mapping.get(vid, vid), t) for oid, (vid, t) in assignments.items()}
    return _swap_for_continuity(problem, assignments, vehicles, orders)


def _swap_for_continuity(
    problem: Problem, assignments: dict[str, tuple[str, int]], vehicles: list[Vehicle], orders: dict[str, Order]
) -> dict[str, tuple[str, int]]:
    """Give two vehicles each other's whole day when that puts more outlets back on the vehicle (and
    driver) that usually serves them and both days stay legal. Trips, km and service are unchanged,
    so the plan is just as good; the standing routes simply survive the time-limited search, which
    otherwise sometimes hands a route to a different but compatible vehicle."""
    from .validate import validate_allocation

    by_vid = {v.id: v for v in vehicles}
    out = dict(assignments)
    sched: dict[str, list[str]] = defaultdict(list)
    for oid, (vid, _t) in out.items():
        if vid in by_vid and oid in orders:
            sched[vid].append(oid)

    def kept(oids: list[str], vid: str) -> int:
        return sum(1 for oid in oids if orders[oid].usual_vehicle == vid)

    for _ in range(3):
        improved = False
        ids = sorted(by_vid)
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                sa, sb = sched.get(a, []), sched.get(b, [])
                if not sa and not sb:
                    continue
                if kept(sa, b) + kept(sb, a) <= kept(sa, a) + kept(sb, b):
                    continue
                trial = {oid: (b, out[oid][1]) for oid in sa} | {oid: (a, out[oid][1]) for oid in sb}
                problems = validate_allocation(
                    [orders[oid] for oid in trial],
                    {a: by_vid[a], b: by_vid[b]},
                    problem.districts,
                    problem.allowance,
                    trial,
                    budget_fresh=problem.budget_fresh,
                    budget_day=problem.budget_day,
                    max_trips=problem.max_trips,
                )
                if problems:
                    continue
                out.update(trial)
                sched[a], sched[b] = sb, sa
                improved = True
        if not improved:
            break
    return out


# ---------------------------------------------------------------------------------------------
# the CP-SAT model
# ---------------------------------------------------------------------------------------------


def solve_depot(problem: Problem, depot: str, hint: dict[str, tuple[str, int]] | None = None) -> DepotResult:
    """Solve one depot. Without a hint, decompose first:

    phase A  the cold chain alone (chilled orders on refrigerated vehicles): small, solves to
             optimality in well under a second, and it is where the hard choices are made
    phase B  the full model (all orders, all vehicles) seeded with phase A plus a greedy
             completion for ambient orders; CP-SAT polishes the joint plan in the time left
    """
    t0 = time.perf_counter()
    orders = [o for o in problem.orders if o.depot == depot and o.id not in problem.force_deferred]
    vehicles = [v for v in problem.vehicles if v.depot == depot and v.available]
    if hint is not None or not any(o.temp == "chilled" for o in orders):
        return _solve(problem, depot, orders, vehicles, hint, problem.time_limit_s, t0)
    cold_orders = [o for o in orders if o.temp == "chilled"]
    reefers = [v for v in vehicles if v.temp == "reefer"]
    tl = problem.time_limit_s
    ra = _solve(problem, depot, cold_orders, reefers, None, max(0.3, min(tl * 0.35, 3.0)), t0)
    # phase B: keep the cold-chain decisions, place everything else around them
    comp = {o.id: [v for v in vehicles if fits_alone(o, v, problem)] for o in orders}
    seed = dict(ra.assignments)
    seed.update(greedy(problem, depot, comp, fixed=seed))
    pinned = replace(problem, pins=list(problem.pins) + [Pin(oid, vid, t) for oid, (vid, t) in ra.assignments.items()])
    left = max(0.3, (tl - (time.perf_counter() - t0)) * 0.6)
    rb = _solve(pinned, depot, orders, vehicles, seed, left, t0)
    best, best_val, how = seed, objective_of(problem, seed), "greedy completion"
    if rb.status in ("OPTIMAL", "FEASIBLE") and objective_of(problem, rb.assignments) >= best_val:
        best, best_val, how = rb.assignments, objective_of(problem, rb.assignments), f"phase B {rb.status}"
    # phase C: joint polish without the cold-chain pins, seeded with the best so far
    left = tl - (time.perf_counter() - t0)
    rc = None
    if left > 0.5:
        rc = _solve(problem, depot, orders, vehicles, best, left, t0)
        if rc.status in ("OPTIMAL", "FEASIBLE") and objective_of(problem, rc.assignments) >= best_val:
            best, best_val, how = rc.assignments, objective_of(problem, rc.assignments), f"phase C {rc.status}"
    final = rc or rb
    final.assignments = relabel_for_continuity(problem, depot, best, {p.vehicle_id for p in problem.pins})
    final.objective = best_val
    final.bound = max(best_val, (rc.bound if rc and rc.bound else rb.bound) or best_val)
    final.status = "OPTIMAL" if (rc and rc.status == "OPTIMAL" and how.startswith("phase C")) else "FEASIBLE"
    final.solve_ms = int((time.perf_counter() - t0) * 1000)
    final.notes.append(f"phase A (cold chain) {ra.status} {ra.solve_ms} ms; kept {how}")
    return final


def objective_of(problem: Problem, assignments: dict[str, tuple[str, int]]) -> float:
    """Evaluate the stage-1 objective for an assignment (same terms as the CP-SAT model)."""
    by_id = {o.id: o for o in problem.orders}
    total = 0.0
    trips: dict[tuple[str, int], list[Order]] = defaultdict(list)
    for oid, key in assignments.items():
        o = by_id[oid]
        total += order_score(o, problem.policy)
        if o.usual_vehicle and o.usual_vehicle == key[0]:
            total += CONTINUITY_BONUS
        trips[key].append(o)
    fresh_trips: dict[str, int] = defaultdict(int)
    for (vid, _), g in trips.items():
        d = problem.districts[g[0].district]
        total -= TRIP_COST
        total -= KM_COST * (round(2 * d.d2d_km - d.inter_km) + len(g) * round(d.inter_km))
        if g[0].brand == "Fresh":
            fresh_trips[vid] += 1
    total -= SECOND_FRESH_TRIP_COST * sum(1 for n in fresh_trips.values() if n >= 2)
    return total


def _solve(
    problem: Problem,
    depot: str,
    orders: list[Order],
    vehicles: list[Vehicle],
    hint: dict[str, tuple[str, int]] | None,
    time_limit_s: float,
    t0: float,
) -> DepotResult:
    slots = range(1, problem.max_trips + 1)
    notes: list[str] = []

    model = cp_model.CpModel()
    comp: dict[str, list[Vehicle]] = {o.id: [v for v in vehicles if fits_alone(o, v, problem)] for o in orders}

    pins = {p.order_id: (p.vehicle_id, p.trip_no) for p in problem.pins}
    by_vid = {v.id: v for v in vehicles}
    pinned_vids = {vid for vid, _ in pins.values()}
    for o in orders:
        if o.id in pins and pins[o.id][0] in by_vid:
            v = by_vid[pins[o.id][0]]
            if v not in comp[o.id]:
                comp[o.id].append(v)  # already on board: keep it even if it no longer "fits alone"
                notes.append(f"pin {o.id}->{v.id} kept although it no longer fits alone")

    vcombos: dict[str, list[tuple[str, str]]] = {}
    for v in vehicles:
        cs = sorted({o.combo for o in orders if v in comp[o.id]})
        vcombos[v.id] = cs
    combo_index = {c: i + 1 for i, c in enumerate(sorted({o.combo for o in orders}))}

    y: dict[tuple[str, int, tuple[str, str]], cp_model.IntVar] = {}
    u: dict[tuple[str, int], cp_model.IntVar] = {}
    for v in vehicles:
        for t in slots:
            u[(v.id, t)] = model.NewBoolVar(f"u_{v.id}_{t}")
            ys = []
            for c in vcombos[v.id]:
                y[(v.id, t, c)] = model.NewBoolVar(f"y_{v.id}_{t}_{c[0]}_{c[1]}")
                ys.append(y[(v.id, t, c)])
            if ys:
                model.Add(sum(ys) == u[(v.id, t)])
            else:
                model.Add(u[(v.id, t)] == 0)
        if v.id in pinned_vids:
            # trips already loading or on the road keep their numbers; slot symmetry rules could
            # contradict them and make the whole depot infeasible
            continue
        for t in slots:
            if t > 1:
                model.AddImplication(u[(v.id, t)], u[(v.id, t - 1)])
        if len(slots) >= 2 and vcombos[v.id]:
            # order a vehicle's two trips by combo index (equal combos allowed)
            i1 = sum(combo_index[c] * y[(v.id, 1, c)] for c in vcombos[v.id])
            i2 = sum(combo_index[c] * y[(v.id, 2, c)] for c in vcombos[v.id])
            model.Add(i1 <= i2).OnlyEnforceIf(u[(v.id, 2)])

    x: dict[tuple[str, str, int], cp_model.IntVar] = {}
    served: dict[str, list] = {}
    for o in orders:
        terms = []
        for v in comp[o.id]:
            for t in slots:
                var = model.NewBoolVar(f"x_{o.id}_{v.id}_{t}")
                x[(o.id, v.id, t)] = var
                model.AddImplication(var, y[(v.id, t, o.combo)])
                terms.append(var)
        if terms:
            model.AddAtMostOne(terms)
        served[o.id] = terms
        if o.id in problem.force_served:
            if not terms:
                return DepotResult(
                    depot, "INFEASIBLE", 0.0, 0.0, {}, int((time.perf_counter() - t0) * 1000), notes=["no vehicle can carry it"]
                )
            model.Add(sum(terms) == 1)

    # a trip slot serves a combo only if it carries at least one of that combo's orders
    # (tightens the LP relaxation considerably: no fractional phantom trips)
    combo_orders: dict[tuple[str, int, tuple[str, str]], list] = defaultdict(list)
    order_combo = {o.id: o.combo for o in orders}
    for (oid, vid, t), var in x.items():
        combo_orders[(vid, t, order_combo[oid])].append(var)
    for key, yv in y.items():
        xs = combo_orders.get(key)
        if xs:
            model.Add(yv <= sum(xs))
        else:
            model.Add(yv == 0)

    for oid, (vid, tno) in pins.items():
        key = (oid, vid, tno)
        if key in x:
            model.Add(x[key] == 1)
        elif oid in comp:
            notes.append(f"pin {oid}->{vid}/{tno} ignored (vehicle unavailable)")

    km_obj_terms = []
    for v in vehicles:
        fresh_terms, day_terms, km_terms = [], [], []
        for t in slots:
            on_trip = [(o, x[(o.id, v.id, t)]) for o in orders if (o.id, v.id, t) in x]
            if on_trip:
                model.Add(sum(_up(o.volume_m3, VOL_SCALE) * var for o, var in on_trip) <= _down(v.volume_cap_m3, VOL_SCALE) * u[(v.id, t)])
                model.Add(
                    sum(_up(o.weight_kg, WEIGHT_SCALE) * var for o, var in on_trip) <= _down(v.weight_cap_kg, WEIGHT_SCALE) * u[(v.id, t)]
                )
            for c in vcombos[v.id]:
                d = problem.districts[c[1]]
                coef = d.d2d_min - d.inter_min
                (fresh_terms if c[0] == "Fresh" else day_terms).append(coef * y[(v.id, t, c)])
                km_coef = 2 * d.d2d_km - d.inter_km
                km_terms.append(_up(km_coef, KM_SCALE) * y[(v.id, t, c)])
                km_obj_terms.append(int(round(km_coef)) * y[(v.id, t, c)])
            for o, var in on_trip:
                d = problem.districts[o.district]
                coef = d.inter_min + problem.allowance[(o.brand, o.dock_type)]
                (fresh_terms if o.brand == "Fresh" else day_terms).append(coef * var)
                km_terms.append(_up(d.inter_km, KM_SCALE) * var)
                km_obj_terms.append(int(round(d.inter_km)) * var)
        if fresh_terms:
            model.Add(sum(fresh_terms) <= problem.budget_fresh)
        if day_terms:
            model.Add(sum(day_terms) <= problem.budget_day)
        if km_terms and v.km_per_l > 0:
            model.Add(sum(km_terms) <= _down(v.fuel_left_l * v.km_per_l, KM_SCALE))

    # symmetry breaking between interchangeable vehicles (not for vehicles carrying pins)
    classes: dict[tuple, list[Vehicle]] = defaultdict(list)
    for v in vehicles:
        if v.id not in pinned_vids:
            classes[vehicle_class(v, problem)].append(v)
    n_sym = 0
    for members in classes.values():
        members.sort(key=lambda v: v.id)
        for a, b in zip(members, members[1:], strict=False):
            model.Add(sum(u[(a.id, t)] for t in slots) >= sum(u[(b.id, t)] for t in slots))
            n_sym += 1

    obj = []
    for o in orders:
        if served[o.id]:
            obj.append(order_score(o, problem.policy) * sum(served[o.id]))
        if o.usual_vehicle:
            for t in slots:
                key = (o.id, o.usual_vehicle, t)
                if key in x:
                    obj.append(CONTINUITY_BONUS * x[key])
    # a second pre-dawn trip usually runs late (windows close 07:30-08:00): only use one when it
    # serves something no first trip can
    for v in vehicles:
        fresh_y = [y[(v.id, t, c)] for t in slots for c in vcombos[v.id] if c[0] == "Fresh"]
        if len(slots) >= 2 and fresh_y:
            two = model.NewBoolVar(f"twofresh_{v.id}")
            model.Add(sum(fresh_y) - 1 <= two)
            obj.append(-SECOND_FRESH_TRIP_COST * two)
    obj.append(-TRIP_COST * sum(u.values()))
    obj.append(-KM_COST * sum(km_obj_terms))
    model.Maximize(sum(obj))

    start = hint
    if start is None:
        start = greedy(problem, depot, {o.id: comp[o.id] for o in orders})
    if start:
        for (oid, vid, t), var in x.items():
            model.AddHint(var, 1 if start.get(oid) == (vid, t) else 0)

    solver = cp_model.CpSolver()
    p = solver.parameters
    if problem.deterministic:
        p.num_workers = 1
        p.max_deterministic_time = max(1.0, time_limit_s * 2)
    else:
        p.num_workers = max(1, problem.workers)
        p.max_time_in_seconds = max(0.2, time_limit_s)
    p.random_seed = problem.seed
    status = solver.Solve(model)
    name = solver.StatusName(status)
    assignments: dict[str, tuple[str, int]] = {}
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for (oid, vid, t), var in x.items():
            if solver.Value(var):
                assignments[oid] = (vid, t)
        obj_val, bound = solver.ObjectiveValue(), solver.BestObjectiveBound()
        assignments = relabel_for_continuity(problem, depot, assignments, pinned_vids)
    else:
        obj_val, bound = 0.0, 0.0
    notes.append(f"symmetry constraints: {n_sym}")
    return DepotResult(depot, name, obj_val, bound, assignments, int((time.perf_counter() - t0) * 1000), len(x) + len(y), notes)
