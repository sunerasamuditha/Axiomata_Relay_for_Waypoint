"""relay_engine: Waypoint delivery planning (allocation, timetable, explanations, validation)."""

from .core import (
    BUDGET_DAY_MIN,
    BUDGET_FRESH_MIN,
    MAX_TRIPS_PER_VEHICLE,
    POLICIES,
    Deferral,
    District,
    Order,
    Pin,
    Problem,
    Solution,
    StopTime,
    TripPlan,
    Vehicle,
    hhmm,
    parse_hhmm,
    parse_window,
)
from .planner import build_trips, kpis, plan
from .policies import POLICY_LABEL, POLICY_SUB, order_score
from .standards import trip_km, trip_litres, trip_minutes
from .timetable import stop_window
from .validate import MoveCheck, Violation, check_move, validate_allocation

__all__ = [
    "BUDGET_DAY_MIN",
    "BUDGET_FRESH_MIN",
    "MAX_TRIPS_PER_VEHICLE",
    "POLICIES",
    "POLICY_LABEL",
    "POLICY_SUB",
    "Deferral",
    "District",
    "MoveCheck",
    "Order",
    "Pin",
    "Problem",
    "Solution",
    "StopTime",
    "TripPlan",
    "Vehicle",
    "Violation",
    "build_trips",
    "check_move",
    "hhmm",
    "kpis",
    "order_score",
    "parse_hhmm",
    "parse_window",
    "plan",
    "stop_window",
    "trip_km",
    "trip_litres",
    "trip_minutes",
    "validate_allocation",
]

__version__ = "1.0.0"
