"""The published planning standard (trip time, distance, fuel) and reference loaders.

`trip_minutes` is a line-for-line port of `trip_time()` in the organisers' `check_allocation.py`:
outbound travel once, inter-stop travel (n - 1) times, plus the service allowance per stop.
The return leg is not counted in minutes ("the stated budgets already allow for it") but it is
counted in kilometres, because route distance is what consumes the weekly fuel quota.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Sequence
from pathlib import Path

from .core import District, Order, Vehicle, parse_hhmm, parse_window


def trip_minutes(district: District, brand: str, docks: Sequence[str], allowance: dict[tuple[str, str], int]) -> int:
    n = len(docks)
    if n == 0:
        return 0
    return district.d2d_min + (n - 1) * district.inter_min + sum(allowance[(brand, d)] for d in docks)


def trip_km(district: District, n_stops: int) -> float:
    if n_stops == 0:
        return 0.0
    return 2 * district.d2d_km + (n_stops - 1) * district.inter_km


def trip_litres(district: District, n_stops: int, km_per_l: float) -> float:
    return trip_km(district, n_stops) / km_per_l if km_per_l else 0.0


def budget_for(brand: str, budget_fresh: int, budget_day: int) -> int:
    return budget_fresh if brand == "Fresh" else budget_day


# ---------------------------------------------------------------------------------------------
# CSV loaders (used by the Task 2B CLI, tests and the API seed)
# ---------------------------------------------------------------------------------------------


def _rows(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_districts(path: Path) -> dict[str, District]:
    out: dict[str, District] = {}
    for r in _rows(path):
        out[r["district"]] = District(
            name=r["district"],
            depot=r["depot"],
            d2d_min=int(float(r["depot_to_district_freeflow_min"])),
            inter_min=int(float(r["inter_stop_freeflow_min"])),
            d2d_km=float(r["depot_to_district_km"]),
            inter_km=float(r["inter_stop_km"]),
        )
    return out


def load_allowance(path: Path) -> dict[tuple[str, str], int]:
    return {(r["brand"], r["dock_type"]): int(float(r["service_allowance_min"])) for r in _rows(path)}


def load_vehicles(path: Path, available: Iterable[str] | None = None, fuel_used: dict[str, float] | None = None) -> list[Vehicle]:
    avail = set(available) if available is not None else None
    fuel_used = fuel_used or {}
    out = []
    for r in _rows(path):
        quota = float(r["weekly_fuel_quota_l"])
        out.append(
            Vehicle(
                id=r["vehicle_id"],
                type=r["type"],
                temp=r["temp"],
                weight_cap_kg=float(r["weight_cap_kg"]),
                volume_cap_m3=float(r["volume_cap_m3"]),
                km_per_l=float(r["km_per_l"]),
                fuel_left_l=max(0.0, quota - fuel_used.get(r["vehicle_id"], 0.0)),
                depot=r["depot"],
                available=(avail is None or r["vehicle_id"] in avail),
            )
        )
    return out


def load_outlets(path: Path) -> dict[str, dict[str, str]]:
    return {r["outlet_id"]: r for r in _rows(path)}


def order_from_row(r: dict[str, str], *, oid: str | None = None, label: str = "") -> Order:
    """Build an Order from a row shaped like task2b_peak_day_scenarios.csv."""
    return Order(
        id=oid or r["order_ref"],
        outlet_id=r["outlet_id"],
        brand=r["brand"],
        district=r["district"],
        depot=r["depot"],
        dock_type=r["dock_type"],
        parking=r["parking_constraint"],
        temp=r["temp_requirement"],
        weight_kg=float(r["order_weight_kg"]),
        volume_m3=float(r["order_volume_m3"]),
        window_open=parse_hhmm(r["window_open_time"]) or 0,
        window_close=parse_hhmm(r["window_close_time"]) or 1439,
        mall_window=parse_window(r.get("mall_window") or None),
        deferred_yesterday=str(r.get("deferred_yesterday", "0")) in ("1", "True", "true"),
        days_since_served=int(float(r.get("days_since_last_served") or 1)),
        label=label or r["outlet_id"],
    )
