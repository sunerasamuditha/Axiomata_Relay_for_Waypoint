"""Service-time and lateness predictions for planned stops.

    from relay_ml import get_predictor
    preds = get_predictor().predict([StopFeatures(...), ...])   # -> [(service_min, late_prob), ...]

If the trained LightGBM boosters are present (``models/service_min.txt``, ``models/late_prob.txt``)
they are used; otherwise a transparent heuristic is used so the product works on a fresh clone
(the competition training data is not redistributed with this repository).
"""

from __future__ import annotations

import json
import math
import os
import threading
from pathlib import Path

from .features import FEATURES, StopFeatures

MODEL_DIR = Path(os.environ.get("RELAY_MODEL_DIR", Path(__file__).resolve().parent.parent / "models"))

# Planning-standard handling minutes (service_allowance.csv); used by the heuristic only.
_ALLOWANCE = {
    ("Fresh", "rear_dock"): 15,
    ("Fresh", "street"): 16,
    ("Fresh", "mall_bay"): 18,
    ("Style", "rear_dock"): 38,
    ("Style", "street"): 46,
    ("Style", "mall_bay"): 59,
    ("Tech", "rear_dock"): 43,
    ("Tech", "street"): 55,
    ("Tech", "mall_bay"): 55,
}
# Historical Fresh late rate by district (2024-01 to 2026-02), for the heuristic prior.
_DISTRICT_RISK = {
    "Colombo": 0.17,
    "Gampaha": 0.13,
    "Kalutara": 0.20,
    "Galle": 0.18,
    "Matara": 0.15,
    "Kurunegala": 0.14,
    "Puttalam": 0.22,
    "Kandy": 0.08,
    "Matale": 0.11,
    "Nuwara Eliya": 0.37,
    "Badulla": 0.42,
    "Kegalle": 0.15,
}


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


class Predictor:
    def __init__(self, model_dir: Path = MODEL_DIR):
        self.model_dir = Path(model_dir)
        self.kind = "heuristic"
        self.meta: dict = {}
        self._svc = self._late = None
        svc, late = self.model_dir / "service_min.txt", self.model_dir / "late_prob.txt"
        if svc.exists() and late.exists():
            try:
                import lightgbm as lgb  # optional dependency at runtime

                self._svc = lgb.Booster(model_file=str(svc))
                self._late = lgb.Booster(model_file=str(late))
                meta = self.model_dir / "meta.json"
                self.meta = json.loads(meta.read_text()) if meta.exists() else {}
                if self.meta.get("features", FEATURES) != FEATURES:
                    raise ValueError("model was trained on a different feature list")
                self.kind = "lightgbm"
            except Exception as exc:  # pragma: no cover - fall back rather than fail the app
                self._svc = self._late = None
                self.meta = {"load_error": str(exc)}
                self.kind = "heuristic"

    # ------------------------------------------------------------------------------------------
    def predict(self, rows: list[StopFeatures]) -> list[tuple[float, float]]:
        if not rows:
            return []
        if self.kind == "lightgbm":
            import numpy as np

            X = np.array([r.row() for r in rows], dtype=float)
            svc = self._svc.predict(X)
            late = self._late.predict(X)
            return [(round(max(1.0, float(s)), 1), round(min(0.99, max(0.01, float(p))), 3)) for s, p in zip(svc, late, strict=False)]
        return [self._heuristic(r) for r in rows]

    @staticmethod
    def _heuristic(r: StopFeatures) -> tuple[float, float]:
        allow = _ALLOWANCE.get((r.brand, r.dock_type), r.allowance or 15)
        # bigger drops take longer; the allowance assumes a typical drop
        size = {"Fresh": 0.015, "Style": 0.06, "Tech": 0.9}.get(r.brand, 0.02) * max(
            0.0, r.units - {"Fresh": 60, "Style": 40, "Tech": 6}.get(r.brand, 40)
        )
        service = allow + size
        slack = r.window_close - r.planned_arrival
        prior = _DISTRICT_RISK.get(r.district, 0.15)
        logit = math.log(prior / (1 - prior)) - slack / 22.0 + 0.6 * r.monsoon + 0.04 * r.seq
        logit += (100 - r.speed_index) / 25.0 + (100 - r.disruption_index) / 15.0
        return (round(service, 1), round(min(0.97, max(0.02, _sigmoid(logit))), 3))

    def describe(self) -> dict:
        return {"kind": self.kind, **{k: v for k, v in self.meta.items() if k in ("metrics", "trained_at", "rows", "load_error")}}


_lock = threading.Lock()
_instance: Predictor | None = None


def get_predictor() -> Predictor:
    global _instance
    with _lock:
        if _instance is None:
            _instance = Predictor()
        return _instance
