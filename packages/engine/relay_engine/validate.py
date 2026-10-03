"""Feasibility validator.

`validate_allocation` mirrors, rule for rule, the organisers' Task 2B checker
(`check_allocation.py`): workshop vehicles, home depot, one brand and one district per trip,
refrigeration, van-only access, volume and weight per trip, at most two trips per vehicle,
the Fresh pre-dawn budget (270 min) and the daytime budget (480 min). We add one rule the
checker leaves out but the booklet requires: the weekly fuel quota.

`check_move` answers the dispatcher's drag-and-drop question ("can this order go on that trip?")
with the same rules and a sentence a dispatcher can act on.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .core import BUDGET_DAY_MIN, BUDGET_FRESH_MIN, MAX_TRIPS_PER_VEHICLE, District, Order, Vehicle
from .standards import budget_for, trip_litres, trip_minutes

EPS = 1e-6


@dataclass
class Violation:
    rule: str
    vehicle_id: str
    trip_no: int | None
    message: str


def validate_allocation(
    orders: list[Order],
    vehicles: dict[str, Vehicle],
    districts: dict[str, District],
    allowance: dict[tuple[str, str], int],
    assignments: dict[str, tuple[str, int]],
    *,
    budget_fresh: int = BUDGET_FRESH_MIN,
    budget_day: int = BUDGET_DAY_MIN,
    max_trips: int = MAX_TRIPS_PER_VEHICLE,
    check_fuel: bool = True,
) -> list[Violation]:
    by_id = {o.id: o for o in orders}
    errors: list[Violation] = []

    trips: dict[tuple[str, int], list[Order]] = defaultdict(list)
    for oid, (vid, tno) in assignments.items():
        if oid not in by_id:
            errors.append(Violation("unknown_order", vid, tno, f"{oid} is not an order of this plan"))
            continue
        if vid not in vehicles:
            errors.append(Violation("unknown_vehicle", vid, tno, f"unknown vehicle_id {vid}"))
            continue
        if tno not in (1, 2):
            errors.append(Violation("trip_id", vid, tno, "trip_id must be 1 or 2"))
            continue
        trips[(vid, tno)].append(by_id[oid])

    for (vid, tno), g in sorted(trips.items()):
        v = vehicles[vid]
        tag = f"[{vid} trip {tno}]"
        if not v.available:
            errors.append(Violation("workshop", vid, tno, f"{tag} vehicle is in the workshop that day"))
            continue
        depots = {o.depot for o in g}
        if len(depots) > 1 or next(iter(depots)) != v.depot:
            errors.append(Violation("depot", vid, tno, f"{tag} vehicle is based at {v.depot} but carries orders for {sorted(depots)}"))
        brands = {o.brand for o in g}
        if len(brands) > 1:
            errors.append(Violation("brand", vid, tno, f"{tag} mixes brands: {sorted(brands)} (one brand per trip)"))
        dists = {o.district for o in g}
        if len(dists) > 1:
            errors.append(Violation("district", vid, tno, f"{tag} mixes districts: {sorted(dists)} (one district per trip)"))
        if any(o.temp == "chilled" for o in g) and v.temp != "reefer":
            errors.append(Violation("reefer", vid, tno, f"{tag} carries chilled orders on a non-refrigerated vehicle"))
        if any(o.parking == "van_only" for o in g) and v.type != "van":
            errors.append(Violation("van_only", vid, tno, f"{tag} sends a {v.type} to a van_only outlet"))
        vol = sum(o.volume_m3 for o in g)
        wt = sum(o.weight_kg for o in g)
        if vol > v.volume_cap_m3 + EPS:
            errors.append(Violation("volume", vid, tno, f"{tag} volume {vol:.1f} m3 exceeds capacity {v.volume_cap_m3} m3"))
        if wt > v.weight_cap_kg + EPS:
            errors.append(Violation("weight", vid, tno, f"{tag} weight {wt:.0f} kg exceeds capacity {v.weight_cap_kg} kg"))

    per_vehicle: dict[str, dict[int, list[Order]]] = defaultdict(dict)
    for (vid, tno), g in trips.items():
        per_vehicle[vid][tno] = g
    for vid, ts in sorted(per_vehicle.items()):
        v = vehicles[vid]
        if len(ts) > max_trips:
            errors.append(Violation("trips", vid, None, f"[{vid}] {len(ts)} trips; a vehicle runs at most {max_trips} a day"))
        fresh_t = other_t = 0.0
        litres = 0.0
        for g in ts.values():
            if len({o.district for o in g}) > 1 or len({o.brand for o in g}) > 1:
                continue  # already reported
            d = districts[g[0].district]
            tt = trip_minutes(d, g[0].brand, [o.dock_type for o in g], allowance)
            if g[0].brand == "Fresh":
                fresh_t += tt
            else:
                other_t += tt
            litres += trip_litres(d, len(g), v.km_per_l)
        if fresh_t > budget_fresh + EPS:
            errors.append(
                Violation(
                    "fresh_window", vid, None, f"[{vid}] Fresh trips total {fresh_t:.0f} min; the pre-dawn window is {budget_fresh} min"
                )
            )
        if other_t > budget_day + EPS:
            errors.append(
                Violation("day_window", vid, None, f"[{vid}] daytime trips total {other_t:.0f} min; the daytime window is {budget_day}")
            )
        if check_fuel and litres > v.fuel_left_l + EPS:
            errors.append(
                Violation("fuel", vid, None, f"[{vid}] needs {litres:.0f} L but has {v.fuel_left_l:.0f} L of this week's fuel quota left")
            )
    return errors


@dataclass
class MoveCheck:
    ok: bool
    message: str
    rule: str = ""
    volume: float = 0.0
    weight: float = 0.0
    minutes: int = 0
    budget: int = 0


def check_move(
    order: Order,
    vehicle: Vehicle,
    trip_no: int,
    vehicle_trips: dict[int, list[Order]],
    districts: dict[str, District],
    allowance: dict[tuple[str, str], int],
    *,
    budget_fresh: int = BUDGET_FRESH_MIN,
    budget_day: int = BUDGET_DAY_MIN,
    max_trips: int = MAX_TRIPS_PER_VEHICLE,
    trip_brand: str | None = None,
    trip_district: str | None = None,
) -> MoveCheck:
    """Would `order` fit on `vehicle` trip `trip_no`, given what the vehicle already carries?

    `vehicle_trips` maps trip number -> orders currently on that trip (without `order`).
    `trip_brand` / `trip_district` describe an existing empty trip slot, if any.
    Messages are written for a dispatcher, not a developer.
    """
    name = order.label or order.outlet_id
    if not vehicle.available:
        return MoveCheck(False, f"{vehicle.id} is in the workshop today.", "workshop")
    if order.depot != vehicle.depot:
        return MoveCheck(False, f"{vehicle.id} is based at {vehicle.depot}. {name} is served from {order.depot}.", "depot")
    if order.temp == "chilled" and vehicle.temp != "reefer":
        return MoveCheck(False, f"Chilled goods need a refrigerated vehicle. {vehicle.id} is a dry {vehicle.type}.", "reefer")
    if order.parking == "van_only" and vehicle.type != "van":
        return MoveCheck(False, f"{name} is van-only. Trucks can't reach it.", "van_only")
    cur = list(vehicle_trips.get(trip_no, []))
    brand = cur[0].brand if cur else (trip_brand or order.brand)
    district = cur[0].district if cur else (trip_district or order.district)
    if order.brand != brand or order.district != district:
        return MoveCheck(
            False, f"One brand and one district per trip. {vehicle.id} trip {trip_no} carries {brand} for {district}.", "combo"
        )
    used_slots = {t for t, g in vehicle_trips.items() if g}
    if trip_no not in used_slots and len(used_slots) >= max_trips:
        return MoveCheck(False, f"{vehicle.id} already runs {max_trips} trips today.", "trips")
    g = cur + [order]
    vol = sum(o.volume_m3 for o in g)
    wt = sum(o.weight_kg for o in g)
    if vol > vehicle.volume_cap_m3 + EPS:
        return MoveCheck(False, f"Over volume: {vol:.1f} of {vehicle.volume_cap_m3:.1f} m³ on {vehicle.id}.", "volume", vol, wt)
    if wt > vehicle.weight_cap_kg + EPS:
        return MoveCheck(False, f"Over weight: {wt:,.0f} of {vehicle.weight_cap_kg:,.0f} kg on {vehicle.id}.", "weight", vol, wt)
    budget = budget_for(order.brand, budget_fresh, budget_day)
    used = 0
    litres = 0.0
    for _tno, orders in {**vehicle_trips, trip_no: g}.items():
        if not orders:
            continue
        d = districts[orders[0].district]
        litres += trip_litres(d, len(orders), vehicle.km_per_l)
        if (orders[0].brand == "Fresh") == (order.brand == "Fresh"):
            used += trip_minutes(d, orders[0].brand, [o.dock_type for o in orders], allowance)
    if used > budget:
        label = "Fresh window" if order.brand == "Fresh" else "Daytime"
        return MoveCheck(False, f"{label} budget exceeded: {used} of {budget} minutes for {vehicle.id}.", "time", vol, wt, used, budget)
    if litres > vehicle.fuel_left_l + EPS:
        return MoveCheck(
            False,
            f"Fuel quota: {vehicle.id} would need {litres:.0f} L and has {vehicle.fuel_left_l:.0f} L left this week.",
            "fuel",
            vol,
            wt,
            used,
            budget,
        )
    return MoveCheck(
        True,
        f"{vol:.1f} of {vehicle.volume_cap_m3:.1f} m³ · {wt:,.0f} of {vehicle.weight_cap_kg:,.0f} kg · {used} of {budget} min",
        "",
        vol,
        wt,
        used,
        budget,
    )
