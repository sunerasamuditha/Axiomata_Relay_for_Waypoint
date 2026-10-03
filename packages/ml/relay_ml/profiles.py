"""Per-outlet ordering profiles used to seed realistic demo days.

    uv run python -m relay_ml.profiles --data data/private --out data/demo/outlet_profiles.json

For every outlet and temperature: how often it orders on each weekday, the typical order size
(units, quartiles) and its kg / m3 per unit, plus the vehicle that usually serves it. These are
aggregates of the history (no order-level rows), so the file can ship with the repository and the
demo day is seeded from real ordering behaviour rather than invented numbers.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def build(data: Path, since: str = "2025-06-01") -> dict:
    d = pd.read_csv(data / "deliveries_train.csv")
    d["od"] = pd.to_datetime(d.order_date)
    d["dow"] = d.od.dt.weekday
    recent = d[d.od >= since]
    days_per_dow = recent.groupby("dow").od.nunique()
    out: dict = {}
    for (outlet, temp), g in recent.groupby(["outlet_id", "temp_requirement"]):
        by_dow = g.groupby("dow").od.nunique()
        p = {int(k): round(float(by_dow.get(k, 0) / days_per_dow.get(k, 1)), 3) for k in range(7)}
        veh = g[g.vehicle_id.notna()].vehicle_id
        out.setdefault(outlet, {})[temp] = {
            "p_by_dow": p,
            "units_q25": float(g.order_units.quantile(0.25)),
            "units_med": float(g.order_units.median()),
            "units_q75": float(g.order_units.quantile(0.75)),
            "kg_per_unit": round(float((g.order_weight_kg / g.order_units).median()), 3),
            "m3_per_unit": round(float((g.order_volume_m3 / g.order_units).median()), 4),
            "usual_vehicle": None if veh.empty else str(veh.mode().iloc[0]),
            "orders": int(len(g)),
        }
    return {"since": since, "outlets": out}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("data/private"))
    ap.add_argument("--out", type=Path, default=Path("data/demo/outlet_profiles.json"))
    a = ap.parse_args(argv)
    prof = build(a.data)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(prof, indent=1))
    print(f"wrote {a.out} ({len(prof['outlets'])} outlets)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
