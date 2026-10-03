"""Engine tests: the planning standard, the validator (mirrors the organisers' checker), the
planner's feasibility under every policy, explanations and move checks."""

from __future__ import annotations

from pathlib import Path

import pytest
from relay_engine import Order, Pin, Problem, Vehicle, check_move, plan, trip_minutes, validate_allocation
from relay_engine.standards import load_allowance, load_districts

REF = Path(__file__).resolve().parents[3] / "data" / "reference"


@pytest.fixture(scope="module")
def std():
    return load_districts(REF / "district_travel.csv"), load_allowance(REF / "service_allowance.csv")


def mk(
    oid,
    district="Gampaha",
    brand="Fresh",
    temp="ambient",
    dock="rear_dock",
    parking="normal",
    m3=1.0,
    kg=200.0,
    depot="Peliyagoda",
    rep=False,
    days=1,
):
    return Order(oid, f"OUT-{oid}", brand, district, depot, dock, parking, temp, kg, m3, 300, 480, None, rep, days, None, oid)


def veh(vid, typ="truck", temp="ambient", kg=4000.0, m3=20.0, depot="Peliyagoda", avail=True, fuel=400.0):
    return Vehicle(vid, typ, temp, kg, m3, 5.0, fuel, depot, avail)


def test_trip_minutes_matches_booklet_example(std):
    districts, allowance = std
    # Booklet: Fresh trip to Gampaha, two rear-dock stops and one street stop = 101 minutes
    assert trip_minutes(districts["Gampaha"], "Fresh", ["rear_dock", "rear_dock", "street"], allowance) == 101
    # Booklet: Colombo, four street stops = 112 minutes
    assert trip_minutes(districts["Colombo"], "Fresh", ["street"] * 4, allowance) == 112


def test_validator_flags_every_rule(std):
    districts, allowance = std
    orders = [mk("a", temp="chilled"), mk("b", parking="van_only"), mk("c", district="Colombo"), mk("d", m3=30.0)]
    vehicles = {"V1": veh("V1"), "V2": veh("V2", avail=False), "V3": veh("V3", m3=10.0)}
    errs = validate_allocation(orders, vehicles, districts, allowance, {"a": ("V1", 1), "b": ("V1", 1), "c": ("V1", 1), "d": ("V3", 1)})
    rules = {e.rule for e in errs}
    assert {"reefer", "van_only", "district", "volume"} <= rules
    assert any(e.rule == "workshop" for e in validate_allocation(orders, vehicles, districts, allowance, {"a": ("V2", 1)}))


def test_fresh_budget_is_per_vehicle(std):
    districts, allowance = std
    # two Badulla stops = 239 min; a second Badulla trip blows the 270-minute pre-dawn window
    orders = [mk(f"b{i}", district="Badulla", depot="Kandy") for i in range(4)]
    v = {"K1": veh("K1", depot="Kandy")}
    errs = validate_allocation(orders, v, districts, allowance, {"b0": ("K1", 1), "b1": ("K1", 1), "b2": ("K1", 2), "b3": ("K1", 2)})
    assert any(e.rule == "fresh_window" for e in errs)


@pytest.mark.parametrize("policy", ["throughput", "balanced", "fairness"])
def test_plan_is_feasible_and_explains_deferrals(std, policy):
    districts, allowance = std
    orders = [mk(f"c{i}", temp="chilled", m3=4.0, kg=700.0, rep=(i == 0), days=3 if i == 0 else 1) for i in range(8)]
    orders += [mk(f"a{i}", m3=2.0) for i in range(6)] + [mk("big", brand="Style", m3=45.0, kg=900.0)]
    vehicles = [veh("R1", temp="reefer", m3=12.0, kg=2500.0), veh("D1"), veh("D2")]
    sol = plan(Problem(orders, vehicles, districts, allowance, policy=policy, time_limit_s=2, workers=2), explain_budget_s=2)
    assert not validate_allocation(orders, {v.id: v for v in vehicles}, districts, allowance, sol.assignments)
    assert all(o.id in sol.assignments for o in orders if o.temp == "ambient" and o.brand == "Fresh")
    reasons = {d.order_id: d for d in sol.deferred}
    assert reasons["big"].kind == "unavoidable" and reasons["big"].code == "Too big"
    assert set(reasons) | set(sol.assignments) == {o.id for o in orders}
    if policy == "fairness":
        assert "c0" in sol.assignments  # the repeat skip is always served when it can be


def test_check_move_gives_human_reasons(std):
    districts, allowance = std
    dry = veh("D1")
    chilled = mk("x", temp="chilled")
    r = check_move(chilled, dry, 1, {}, districts, allowance)
    assert not r.ok and "refrigerated" in r.message.lower()
    van_only = mk("y", parking="van_only")
    r = check_move(van_only, dry, 1, {}, districts, allowance)
    assert not r.ok and "van-only" in r.message
    ok = check_move(mk("z"), dry, 1, {1: [mk("w")]}, districts, allowance)
    assert ok.ok and "min" in ok.message


def test_capacity_is_never_undercounted_by_rounding(std):
    """Two 520.4 kg orders weigh 1,040.8 kg: they must not share a 1,040 kg van, even though each
    rounds to 520 kg. (Found by `make check-plan`: the model used to round weights to the nearest kg.)"""
    districts, allowance = std
    orders = [mk(f"w{i}", kg=520.4, m3=0.5) for i in range(4)]
    van = veh("V1", typ="van", kg=1040.0, m3=10.0)
    sol = plan(Problem(orders, [van], districts, allowance, policy="throughput", time_limit_s=2, workers=1), explain_budget_s=1)
    assert not validate_allocation(orders, {van.id: van}, districts, allowance, sol.assignments)
    per_trip: dict[tuple[str, int], float] = {}
    for oid, key in sol.assignments.items():
        per_trip[key] = per_trip.get(key, 0.0) + next(o.weight_kg for o in orders if o.id == oid)
    assert per_trip and all(kg <= van.weight_cap_kg for kg in per_trip.values())


def test_trips_already_on_the_road_keep_their_numbers(std):
    """A re-plan pins trips that are loading or out. Pinning trip 1 to a district that sorts after
    trip 2's used to clash with the slot-ordering symmetry rule and make the depot infeasible."""
    districts, allowance = std
    orders = [mk("k1", district="Kalutara"), mk("c1", district="Colombo"), mk("g1"), mk("g2")]
    vehicles = [veh("D1"), veh("D2")]
    pins = [Pin("k1", "D1", 1), Pin("c1", "D1", 2)]
    sol = plan(Problem(orders, vehicles, districts, allowance, pins=pins, time_limit_s=2, workers=1), explain_budget_s=1)
    assert sol.status in ("OPTIMAL", "FEASIBLE")
    assert sol.assignments["k1"][0] == "D1" and sol.assignments["c1"][0] == "D1"
    assert sol.assignments["k1"][1] != sol.assignments["c1"][1]
    assert {"g1", "g2"} <= set(sol.assignments)
    assert not validate_allocation(orders, {v.id: v for v in vehicles}, districts, allowance, sol.assignments)


def test_standing_routes_survive_the_search(std):
    """Two compatible vans that are not interchangeable (different fuel left): if the search hands the
    hill route to the wrong one, the continuity pass swaps their days back without breaking a rule."""
    from dataclasses import replace

    from relay_engine.model import relabel_for_continuity

    districts, allowance = std
    hill = [replace(mk(f"h{i}", district="Nuwara Eliya", depot="Kandy"), usual_vehicle="K57") for i in range(3)]
    other = [mk(f"o{i}", district="Matale", depot="Kandy") for i in range(2)]
    a, b = veh("K57", depot="Kandy", fuel=120.0), veh("K58", depot="Kandy", fuel=300.0)
    problem = Problem(hill + other, [a, b], districts, allowance)
    wrong = {o.id: ("K58", 1) for o in hill} | {o.id: ("K57", 1) for o in other}
    fixed = relabel_for_continuity(problem, "Kandy", wrong, set())
    assert all(fixed[o.id][0] == "K57" for o in hill) and all(fixed[o.id][0] == "K58" for o in other)
    assert not validate_allocation(hill + other, {"K57": a, "K58": b}, districts, allowance, fixed)
