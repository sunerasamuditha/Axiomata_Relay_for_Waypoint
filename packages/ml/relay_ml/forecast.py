"""Weekly demand forecast for the dispatcher's capacity outlook.

    uv run python -m relay_ml.forecast --data data/private --reference data/reference \
        --start 2026-10-05 --weeks 10 --out data/demo/forecast_weekly.json

Demand is prepared exactly as the Datathon Task 2A brief asks: every order counts once (including
deferred and never-run orders) in the week the store requested it (order_date), with ISO weeks.

Model (transparent seasonal decomposition, robust when extrapolating months ahead):
    volume_per_operating_day(week) = level x seasonal_index(iso_week) x festival_uplift
    weekly_volume = volume_per_operating_day x operating_days(week)
  * level            mean per-operating-day volume over the last 8 complete weeks of history
  * seasonal_index   ratio of a week to its year's mean, averaged over the years observed and
                     smoothed over neighbouring weeks
  * festival_uplift  how much the 9 days before each festival lifted demand historically
The output holds weekly aggregates only (no order-level data), so it may be committed.
A one-season backtest (fit before week 14 of 2025, forecast weeks 14-23) is reported as MAPE.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# Festivals and non-operating days after calendar.csv ends (Government of Sri Lanka 2026 calendar).
EXTRA_FESTIVALS_2026 = {
    "2026-10-25": "vap_poya",
    "2026-11-08": "deepavali",
    "2026-11-24": "il_poya",
    "2026-12-23": "unduvap_poya",
    "2026-12-25": "christmas",
}
EXTRA_NON_OPERATING = {"2026-12-25"}
UPLIFT_FESTIVALS = {"deepavali", "christmas", "new_year", "vesak", "thai_pongal", "poson", "esala"}


def _orders(data: Path) -> pd.DataFrame:
    cols = ["delivery_id", "order_date", "depot", "brand", "temp_requirement", "order_volume_m3"]
    a = pd.read_csv(data / "deliveries_train.csv", usecols=cols)
    b_path = data / "task1_test_inputs.csv"
    frames = [a]
    if b_path.exists():
        frames.append(pd.read_csv(b_path, usecols=cols))
    df = pd.concat(frames).drop_duplicates("delivery_id")
    df["order_date"] = pd.to_datetime(df.order_date)
    return df


def _week_start(d: pd.Series) -> pd.Series:
    return (d - pd.to_timedelta(d.dt.weekday, unit="D")).dt.normalize()


def _operating_days(start: date, cal: pd.DataFrame) -> int:
    n = 0
    for i in range(7):
        d = start + timedelta(days=i)
        ds = d.isoformat()
        row = cal.get(ds)
        if row is not None:
            n += int(row)
        else:
            n += 0 if (d.weekday() == 6 or ds in EXTRA_NON_OPERATING) else 1
    return n


def _festival_in_ramp(start: date, festivals: dict[str, str]) -> list[str]:
    """Festivals whose 9-day run-up overlaps this week."""
    out = []
    for ds, name in festivals.items():
        f = date.fromisoformat(ds)
        if name not in UPLIFT_FESTIVALS:
            continue
        # the festival falls in this week or the next nine days of run-up overlap it
        if f >= start and f - timedelta(days=9) <= start + timedelta(days=6):
            out.append(name)
    return out


def fit(df: pd.DataFrame, cal_ops: dict[str, int], festivals: dict[str, str], cutoff: pd.Timestamp) -> dict:
    hist = df[df.order_date < cutoff].copy()
    hist["week"] = _week_start(hist.order_date)
    hist["chilled"] = np.where(hist.temp_requirement == "chilled", hist.order_volume_m3, 0.0)
    w = hist.groupby(["depot", "brand", "week"]).agg(total=("order_volume_m3", "sum"), chilled=("chilled", "sum")).reset_index()
    w["ops"] = [max(1, _operating_days(x.date(), cal_ops)) for x in w.week]
    w = w[w.ops >= 3]  # ignore weeks broken by data edges or long holidays for the level
    w["per_day"] = w.total / w.ops
    w["chilled_per_day"] = w.chilled / w.ops
    w["iso_week"] = [x.isocalendar()[1] for x in w.week]
    w["year"] = [x.isocalendar()[0] for x in w.week]
    w["fest"] = [bool(_festival_in_ramp(x.date(), festivals)) for x in w.week]

    model: dict = {"groups": {}}
    last_weeks = sorted(w.week.unique())[-9:-1]  # last 8 complete weeks
    for (depot, brand), g in w.groupby(["depot", "brand"]):
        year_mean = g[~g.fest].groupby("year").per_day.mean()
        g = g.assign(ratio=g.per_day / g.year.map(year_mean))
        seas = g[~g.fest].groupby("iso_week").ratio.mean().reindex(range(1, 54))
        seas = seas.interpolate(limit_direction="both").rolling(3, center=True, min_periods=1).mean()
        recent = g[g.week.isin(last_weeks)]
        level = float((recent.per_day / recent.iso_week.map(seas)).mean()) if len(recent) else float(g.per_day.mean())
        chilled_share = float(recent.chilled.sum() / recent.total.sum()) if len(recent) and recent.total.sum() else 0.0
        fest = g[g.fest]
        uplift = float((fest.ratio / fest.iso_week.map(seas)).mean()) if len(fest) else 1.0
        model["groups"][f"{depot}|{brand}"] = {
            "level": level,
            "seasonal": [float(x) for x in seas.values],
            "chilled_share": chilled_share,
            "festival_uplift": max(1.0, uplift if np.isfinite(uplift) else 1.0),
        }
    return model


def predict(model: dict, start: date, weeks: int, cal_ops: dict[str, int], festivals: dict[str, str]) -> list[dict]:
    rows = []
    for i in range(weeks):
        ws = start + timedelta(days=7 * i)
        iso_year, iso_week, _ = ws.isocalendar()
        ops = _operating_days(ws, cal_ops)
        fests = _festival_in_ramp(ws, festivals)
        row = {
            "week_start": ws.isoformat(),
            "iso_year": iso_year,
            "iso_week": iso_week,
            "operating_days": ops,
            "festivals": fests,
            "groups": {},
        }
        for key, g in model["groups"].items():
            per_day = g["level"] * g["seasonal"][iso_week - 1] * (g["festival_uplift"] if fests else 1.0)
            total = per_day * ops
            depot, brand = key.split("|")
            chilled = total * g["chilled_share"] if brand == "Fresh" else 0.0
            row["groups"][key] = {"depot": depot, "brand": brand, "total_m3": round(total, 1), "chilled_m3": round(chilled, 1)}
        rows.append(row)
    return rows


def reefer_day_capacity(data: Path) -> dict[str, float]:
    """Average chilled m3 one refrigerated vehicle delivered per working day, per depot."""
    cols = ["dispatch_date", "depot", "temp_requirement", "order_volume_m3", "vehicle_id", "vehicle_temp", "dispatch_status"]
    d = pd.read_csv(data / "deliveries_train.csv", usecols=cols)
    d = d[(d.temp_requirement == "chilled") & d.vehicle_id.notna()]
    by = d.groupby(["depot", "dispatch_date"]).agg(m3=("order_volume_m3", "sum"), vehicles=("vehicle_id", "nunique"))
    return {k: round(float((g.m3 / g.vehicles).mean()), 2) for k, g in by.groupby(level=0)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("data/private"))
    ap.add_argument("--reference", type=Path, default=Path("data/reference"))
    ap.add_argument("--start", default="2026-10-05")
    ap.add_argument("--weeks", type=int, default=10)
    ap.add_argument("--out", type=Path, default=Path("data/demo/forecast_weekly.json"))
    a = ap.parse_args(argv)

    cal = pd.read_csv(a.reference / "calendar.csv")
    cal_ops = dict(zip(cal.date, cal.is_operating, strict=False))
    festivals = {r.date: r.festival for r in cal.itertuples() if isinstance(r.festival, str)}
    festivals.update(EXTRA_FESTIVALS_2026)
    df = _orders(a.data)

    # backtest: same season one year earlier
    bt_model = fit(df, cal_ops, festivals, pd.Timestamp("2025-03-31"))
    bt = predict(bt_model, date(2025, 3, 31), 10, cal_ops, festivals)
    actual = df[(df.order_date >= "2025-03-31") & (df.order_date < "2025-06-09")].copy()
    actual["week"] = _week_start(actual.order_date).dt.date.astype(str)
    act = actual.groupby(["depot", "brand", "week"]).order_volume_m3.sum()
    errs: dict[str, list[float]] = {"Fresh": [], "Style": [], "Tech": []}
    for r in bt:
        for key, g in r["groups"].items():
            depot, brand = key.split("|")
            y = act.get((depot, brand, r["week_start"]))
            if y:
                errs[brand].append(abs(g["total_m3"] - y) / y)
    by_brand = {b: (round(100 * float(np.mean(e)), 1) if e else None) for b, e in errs.items()}
    allerr = [x for e in errs.values() for x in e]
    mape = round(100 * float(np.mean(allerr)), 1) if allerr else None

    model = fit(df, cal_ops, festivals, pd.Timestamp(df.order_date.max().normalize() + pd.Timedelta(days=1)))
    weeks = predict(model, date.fromisoformat(a.start), a.weeks, cal_ops, festivals)
    out = {
        "method": "seasonal decomposition (level x weekly seasonality x festival uplift), Task 2A demand rules",
        "history_end": str(df.order_date.max().date()),
        "backtest": {"weeks": "2025-W14..W23", "mape_pct": mape, "mape_pct_by_brand": by_brand},
        "reefer_m3_per_vehicle_day": reefer_day_capacity(a.data),
        "weeks": weeks,
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, indent=2))
    print(f"backtest MAPE {mape}% (by brand {by_brand}) · wrote {a.out}")
    for w in weeks:
        ch = sum(g["chilled_m3"] for g in w["groups"].values())
        print(w["week_start"], w["operating_days"], w["festivals"], "chilled m3", round(ch))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
