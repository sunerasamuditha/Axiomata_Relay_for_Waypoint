"""Train the stop service-time and lateness models from the competition history.

    uv run python -m relay_ml.train --data data/private --reference data/reference --out packages/ml/models

Label construction (verified against the data, see docs/PLANNING_ENGINE.md):
  * every order is one stop: deliveries.(route_id, seq_in_route) == route_legs.(route_id, seq)
  * service_min = leave_outlet_time - max(arrival_time, window_open_time)
        (a vehicle that arrives early waits for the window; waiting is not handling time)
  * late        = arrival_time > window_close_time  (strictly after the window closes)

Only plan-time information is used as features (planned arrival, planned leg, position on the
route, the window, the order's size, the outlet's access, the hour's typical traffic and the
date's road disruption). Validation is a time split: train before 2025-11-01, validate after.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .features import CATEGORICAL, CATEGORIES, FEATURES


def _mins(s: pd.Series) -> pd.Series:
    t = s.astype(str).str.split(":", expand=True)
    return t[0].astype(float) * 60 + t[1].astype(float)


def build_frame(data: Path, reference: Path) -> pd.DataFrame:
    d = pd.read_csv(data / "deliveries_train.csv")
    legs = pd.read_csv(data / "route_legs_train.csv")
    outlets = pd.read_csv(reference / "outlets.csv")[["outlet_id", "parking_constraint", "dock_type"]]
    traffic = pd.read_csv(reference / "traffic_speed.csv")
    allow = pd.read_csv(reference / "service_allowance.csv")
    road_path = data / "road_conditions.csv"
    road = pd.read_csv(road_path) if road_path.exists() else None

    d = d[d.route_id.notna()].copy()
    d["seq"] = d.seq_in_route.astype(int)
    legs_cols = [
        "route_id",
        "seq",
        "date",
        "distance_km",
        "planned_depart_time",
        "planned_travel_duration_min",
        "planned_arrival_time",
        "arrival_time",
        "leave_outlet_time",
        "monsoon",
        "dow",
    ]
    m = d.merge(legs[legs_cols], on=["route_id", "seq"], how="inner", suffixes=("", "_leg"))
    m = m.merge(outlets, on="outlet_id", how="left")
    n_stops = legs.groupby("route_id").seq.count().rename("n_stops")
    first_dep = legs[legs.seq == 0].set_index("route_id").planned_depart_time.rename("route_depart")
    m = m.join(n_stops, on="route_id").join(first_dep, on="route_id")

    m["planned_arrival"] = _mins(m.planned_arrival_time_leg if "planned_arrival_time_leg" in m else m.planned_arrival_time)
    m["arrival"] = _mins(m.arrival_time)
    m["leave"] = _mins(m.leave_outlet_time)
    m["window_open"] = _mins(m.window_open_time)
    m["window_close"] = _mins(m.window_close_time)
    m["cum_planned_min"] = m.planned_arrival - _mins(m.route_depart)
    m["service_min"] = (m.leave - np.maximum(m.arrival, m.window_open)).clip(lower=0)
    m["late"] = (m.arrival > m.window_close).astype(int)

    m["hour"] = (m.planned_arrival // 60).astype(int) % 24
    m = m.merge(traffic.rename(columns={"hour": "hour"}), on=["district", "hour", "monsoon"], how="left")
    if road is not None:
        m = m.merge(road, on=["district", "date"], how="left")
    else:
        m["disruption_index"] = 100.0
    m["disruption_index"] = m.disruption_index.fillna(100.0)
    m["speed_index"] = m.speed_index.fillna(100.0)
    m = m.merge(allow.rename(columns={"service_allowance_min": "allowance"}), on=["brand", "dock_type"], how="left")

    m = m.rename(
        columns={
            "temp_requirement": "temp",
            "parking_constraint": "parking",
            "order_units": "units",
            "order_weight_kg": "weight_kg",
            "order_volume_m3": "volume_m3",
            "distance_km": "leg_km",
            "planned_travel_duration_min": "leg_min",
        }
    )
    m["slack_close"] = m.window_close - m.planned_arrival
    m["slack_open"] = m.planned_arrival - m.window_open
    for c, cats in CATEGORIES.items():
        m[c] = pd.Categorical(m[c], categories=cats).codes.astype(float)
        m.loc[m[c] < 0, c] = np.nan
    return m


def main(argv: list[str] | None = None) -> int:
    import lightgbm as lgb
    from sklearn.metrics import log_loss, mean_absolute_error, roc_auc_score  # noqa: F401  (sklearn optional)

    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("data/private"))
    ap.add_argument("--reference", type=Path, default=Path("data/reference"))
    ap.add_argument("--out", type=Path, default=Path("packages/ml/models"))
    ap.add_argument("--split", default="2025-11-01")
    a = ap.parse_args(argv)
    t0 = time.time()
    df = build_frame(a.data, a.reference)
    tr, va = df[df.date < a.split], df[df.date >= a.split]
    X_tr, X_va = tr[FEATURES].astype(float), va[FEATURES].astype(float)
    cat_idx = [FEATURES.index(c) for c in CATEGORICAL]

    common = dict(
        learning_rate=0.05,
        num_leaves=63,
        min_data_in_leaf=40,
        feature_fraction=0.85,
        bagging_fraction=0.85,
        bagging_freq=1,
        verbose=-1,
        seed=7,
    )
    svc_params = {**common, "objective": "l1"}
    late_params = {**common, "objective": "binary"}

    ds = lgb.Dataset(X_tr, tr.service_min, categorical_feature=cat_idx, free_raw_data=False)
    dv = lgb.Dataset(X_va, va.service_min, categorical_feature=cat_idx, reference=ds)
    svc = lgb.train(svc_params, ds, 2000, valid_sets=[dv], callbacks=[lgb.early_stopping(100, verbose=False)])
    ls = lgb.Dataset(X_tr, tr.late, categorical_feature=cat_idx, free_raw_data=False)
    lv = lgb.Dataset(X_va, va.late, categorical_feature=cat_idx, reference=ls)
    late = lgb.train(late_params, ls, 2000, valid_sets=[lv], callbacks=[lgb.early_stopping(100, verbose=False)])

    p_svc = svc.predict(X_va, num_iteration=svc.best_iteration)
    p_late = np.clip(late.predict(X_va, num_iteration=late.best_iteration), 1e-4, 1 - 1e-4)
    prior = float(tr.late.mean())
    metrics = {
        "service_mae": round(float(mean_absolute_error(va.service_min, p_svc)), 3),
        "service_mae_allowance_baseline": round(float(mean_absolute_error(va.service_min, va.allowance)), 3),
        "late_logloss": round(float(log_loss(va.late, p_late)), 4),
        "late_logloss_prior_baseline": round(float(log_loss(va.late, np.full(len(va), prior))), 4),
        "late_auc": round(float(roc_auc_score(va.late, p_late)), 4),
        "valid_rows": int(len(va)),
        "best_iterations": {"service": int(svc.best_iteration), "late": int(late.best_iteration)},
    }
    print(json.dumps(metrics, indent=2))

    # refit on all history with the validated number of rounds
    X_all = df[FEATURES].astype(float)
    svc_f = lgb.train(svc_params, lgb.Dataset(X_all, df.service_min, categorical_feature=cat_idx), max(50, svc.best_iteration))
    late_f = lgb.train(late_params, lgb.Dataset(X_all, df.late, categorical_feature=cat_idx), max(50, late.best_iteration))
    a.out.mkdir(parents=True, exist_ok=True)
    svc_f.save_model(str(a.out / "service_min.txt"))
    late_f.save_model(str(a.out / "late_prob.txt"))
    (a.out / "meta.json").write_text(
        json.dumps(
            {
                "features": FEATURES,
                "categories": CATEGORIES,
                "metrics": metrics,
                "rows": int(len(df)),
                "split": a.split,
                "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "labels": {
                    "service_min": "leave_outlet_time - max(arrival_time, window_open_time)",
                    "late": "arrival_time > window_close_time",
                },
            },
            indent=2,
        )
    )
    print(f"saved models to {a.out} in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
