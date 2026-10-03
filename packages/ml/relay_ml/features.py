"""Stop features that are known when the plan is made (no actuals), shared by training and serving.

The same function builds the feature row in `train.py` (from the historical route legs) and in the
API (from a planned stop), so the model never sees a feature at training time that it cannot have
at planning time.
"""

from __future__ import annotations

from dataclasses import dataclass

CATEGORIES: dict[str, list[str]] = {
    "brand": ["Fresh", "Style", "Tech"],
    "district": [
        "Colombo",
        "Gampaha",
        "Kalutara",
        "Galle",
        "Matara",
        "Kurunegala",
        "Puttalam",
        "Kandy",
        "Matale",
        "Nuwara Eliya",
        "Badulla",
        "Kegalle",
    ],
    "dock_type": ["rear_dock", "street", "mall_bay"],
    "parking": ["normal", "van_only", "mall_dock"],
    "temp": ["ambient", "chilled"],
    "vehicle_type": ["truck", "van"],
    "vehicle_temp": ["ambient", "reefer"],
}

FEATURES: list[str] = [
    "brand",
    "district",
    "dock_type",
    "parking",
    "temp",
    "vehicle_type",
    "vehicle_temp",
    "units",
    "weight_kg",
    "volume_m3",
    "seq",
    "n_stops",
    "planned_arrival",
    "window_open",
    "window_close",
    "slack_close",
    "slack_open",
    "leg_km",
    "leg_min",
    "cum_planned_min",
    "dow",
    "monsoon",
    "speed_index",
    "disruption_index",
    "allowance",
]
CATEGORICAL = list(CATEGORIES)


@dataclass
class StopFeatures:
    brand: str
    district: str
    dock_type: str
    parking: str
    temp: str
    vehicle_type: str
    vehicle_temp: str
    units: float
    weight_kg: float
    volume_m3: float
    seq: int  # 0-based position on the route
    n_stops: int
    planned_arrival: int  # minutes after midnight
    window_open: int
    window_close: int
    leg_km: float
    leg_min: float
    cum_planned_min: float  # planned minutes since depot departure
    dow: int  # 0 = Monday
    monsoon: int
    speed_index: float = 100.0
    disruption_index: float = 100.0
    allowance: float = 15.0

    def row(self) -> list[float]:
        vals: list[float] = []
        for name in FEATURES:
            if name == "slack_close":
                vals.append(float(self.window_close - self.planned_arrival))
            elif name == "slack_open":
                vals.append(float(self.planned_arrival - self.window_open))
            elif name in CATEGORIES:
                cats = CATEGORIES[name]
                v = getattr(self, name)
                vals.append(float(cats.index(v)) if v in cats else float("nan"))
            else:
                vals.append(float(getattr(self, name)))
        return vals


def monsoon_for_month(month: int) -> int:
    """Sri Lanka's monsoon and inter-monsoon months, as encoded in calendar.csv."""
    return 1 if month in (3, 4, 5, 6, 10, 11) else 0
