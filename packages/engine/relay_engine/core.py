"""Plain data types shared by the planner, the validator and the API.

Everything here is deliberately free of database or web concerns so the engine can be
unit-tested in isolation and reused for the Datathon Task 2B peak-day allocation.
Times are minutes after midnight (local Sri Lanka time); capacities are in kg and m3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Brand = Literal["Fresh", "Style", "Tech"]
Temp = Literal["chilled", "ambient"]
Policy = Literal["throughput", "balanced", "fairness"]

POLICIES: tuple[Policy, ...] = ("throughput", "balanced", "fairness")

# Published planning standard (Challenge Booklet, Task 2B "Calculate trip time")
BUDGET_FRESH_MIN = 270  # pre-dawn window 03:30-08:00, all Fresh trips of one vehicle
BUDGET_DAY_MIN = 480  # trading day, Style + Tech trips of one vehicle
MAX_TRIPS_PER_VEHICLE = 2


@dataclass(frozen=True)
class District:
    name: str
    depot: str
    d2d_min: int  # depot_to_district_freeflow_min
    inter_min: int  # inter_stop_freeflow_min
    d2d_km: float
    inter_km: float


@dataclass
class Order:
    id: str
    outlet_id: str
    brand: str
    district: str
    depot: str
    dock_type: str  # rear_dock | street | mall_bay
    parking: str  # normal | van_only | mall_dock
    temp: str  # chilled | ambient
    weight_kg: float
    volume_m3: float
    window_open: int  # minutes after midnight
    window_close: int
    mall_window: tuple[int, int] | None = None
    deferred_yesterday: bool = False
    days_since_served: int = 1
    usual_vehicle: str | None = None
    label: str = ""  # outlet name, used in explanations

    @property
    def combo(self) -> tuple[str, str]:
        return (self.brand, self.district)

    @property
    def is_fresh(self) -> bool:
        return self.brand == "Fresh"


@dataclass
class Vehicle:
    id: str
    type: str  # truck | van
    temp: str  # reefer | ambient
    weight_cap_kg: float
    volume_cap_m3: float
    km_per_l: float
    fuel_left_l: float  # weekly quota minus what this week has used so far
    depot: str
    available: bool = True
    driver: str = ""


@dataclass(frozen=True)
class Pin:
    """Keep an order on a specific vehicle trip (locked trips, already-loaded goods)."""

    order_id: str
    vehicle_id: str
    trip_no: int


@dataclass
class Problem:
    orders: list[Order]
    vehicles: list[Vehicle]
    districts: dict[str, District]
    allowance: dict[tuple[str, str], int]  # (brand, dock_type) -> minutes
    policy: Policy = "balanced"
    pins: list[Pin] = field(default_factory=list)
    force_served: set[str] = field(default_factory=set)  # counterfactual "what if we served it"
    force_deferred: set[str] = field(default_factory=set)
    budget_fresh: int = BUDGET_FRESH_MIN
    budget_day: int = BUDGET_DAY_MIN
    max_trips: int = MAX_TRIPS_PER_VEHICLE
    time_limit_s: float = 6.0
    workers: int = 8
    seed: int = 7
    deterministic: bool = False  # single worker + deterministic time, for reproducible tests


@dataclass
class StopTime:
    order_id: str
    arrive: int
    start: int
    finish: int
    late: bool  # planned arrival after the window closes


@dataclass
class TripPlan:
    vehicle_id: str
    trip_no: int
    depot: str
    brand: str
    district: str
    order_ids: list[str]
    minutes: int  # planning-standard duration (no return leg), as in check_allocation.py
    km: float  # including the return leg, used for fuel
    litres: float
    volume_m3: float
    weight_kg: float
    depart: int | None = None
    return_at: int | None = None
    stops: list[StopTime] = field(default_factory=list)
    code: str = ""  # human code like K5, assigned by the API


@dataclass
class Deferral:
    order_id: str
    kind: Literal["unavoidable", "choice"]
    code: str  # Refrigerated space | Fresh window | Too big | Van access | Fuel quota | ...
    text: str
    cost: dict = field(default_factory=dict)


@dataclass
class Solution:
    status: str
    policy: str
    objective: float
    bound: float
    solve_ms: int
    trips: list[TripPlan]
    assignments: dict[str, tuple[str, int]]  # order_id -> (vehicle_id, trip_no)
    deferred: list[Deferral]
    kpis: dict = field(default_factory=dict)

    @property
    def served_ids(self) -> set[str]:
        return set(self.assignments)

    def trip_of(self, order_id: str) -> TripPlan | None:
        a = self.assignments.get(order_id)
        if not a:
            return None
        for t in self.trips:
            if (t.vehicle_id, t.trip_no) == a:
                return t
        return None


def hhmm(minutes: int | float | None) -> str:
    if minutes is None:
        return "--:--"
    m = int(round(minutes)) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def parse_hhmm(s: str | None) -> int | None:
    if s is None or s == "" or (isinstance(s, float)):
        return None
    h, m = str(s).strip().split(":")[:2]
    return int(h) * 60 + int(m)


def parse_window(s: str | None) -> tuple[int, int] | None:
    """'10:00-12:00' -> (600, 720)."""
    if not s or not isinstance(s, str) or "-" not in s:
        return None
    a, b = s.split("-", 1)
    pa, pb = parse_hhmm(a), parse_hhmm(b)
    if pa is None or pb is None:
        return None
    return (pa, pb)
