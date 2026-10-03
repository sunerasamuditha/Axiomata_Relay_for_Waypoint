"""Datathon Task 2B with the same engine that plans the hackathon app.

    uv run python -m relay_engine.task2b \
        --scenarios data/private/task2b_peak_day_scenarios.csv \
        --fleet data/private/task2b_peak_day_fleet.csv \
        --reference data/reference --policy balanced --time-limit 60 \
        --out data/private/submission_task2b.csv

Writes the submission in the template's exact column order, validates it with our port of the
organisers' feasibility rules, and prints the numbers the written policy needs (limiting
resources, unavoidable vs chosen deferrals and what they cost).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from .core import Problem
from .planner import plan
from .policies import POLICY_LABEL
from .standards import load_allowance, load_districts, load_vehicles, order_from_row
from .validate import validate_allocation


def run(scenarios: Path, fleet: Path, reference: Path, policy: str, time_limit: float, out: Path | None) -> int:
    with open(scenarios, newline="") as fh:
        rows = list(csv.DictReader(fh))
    with open(fleet, newline="") as fh:
        fl = list(csv.DictReader(fh))
    scenario = rows[0]["scenario"]
    available = {r["vehicle_id"] for r in fl if r["status"] == "available" and r["scenario"] == scenario}
    vehicles = load_vehicles(reference / "vehicles.csv", available=available)
    districts = load_districts(reference / "district_travel.csv")
    allowance = load_allowance(reference / "service_allowance.csv")
    orders = [order_from_row(r) for r in rows]
    problem = Problem(orders, vehicles, districts, allowance, policy=policy, time_limit_s=time_limit, workers=8)
    sol = plan(problem, explain_budget_s=max(10.0, time_limit / 2))

    errs = validate_allocation(orders, {v.id: v for v in vehicles}, districts, allowance, sol.assignments, check_fuel=False)
    k = sol.kpis
    print(f"Scenario {scenario} · policy {POLICY_LABEL[policy]} · status {sol.status} · {sol.solve_ms} ms")
    print(
        f"Served {k['served']}/{k['orders']} orders · {k['volume_served_m3']:.1f}/{k['volume_m3']:.1f} m³ · "
        f"chilled {k['chilled_served_m3']:.1f}/{k['chilled_m3']:.1f} m³ · repeat skips {k['repeat_skips']}/{k['repeats_total']}"
    )
    print(f"Trips {k['trips']} on {k['vehicles']} vehicles · {k['km']:.0f} km")
    print("Deferred:")
    for d in sorted(sol.deferred, key=lambda d: (d.kind, d.order_id)):
        print(f"  {d.order_id:8s} {d.kind:11s} {d.code:18s} {d.text}")
    print("FEASIBILITY:", "PASSED - every rule satisfied." if not errs else f"FAILED ({len(errs)})")
    for e in errs[:20]:
        print("  -", e.message)

    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["scenario", "order_ref", "outlet_id", "decision", "vehicle_id", "trip_id"])
            for r in rows:
                a = sol.assignments.get(r["order_ref"])
                if a:
                    w.writerow([r["scenario"], r["order_ref"], r["outlet_id"], "served", a[0], a[1]])
                else:
                    w.writerow([r["scenario"], r["order_ref"], r["outlet_id"], "deferred", "", ""])
        side = out.with_suffix(".explain.json")
        side.write_text(json.dumps({"kpis": k, "deferred": [d.__dict__ for d in sol.deferred]}, indent=2))
        print(f"Wrote {out} and {side}")
    return 1 if errs else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenarios", type=Path, default=Path("data/private/task2b_peak_day_scenarios.csv"))
    ap.add_argument("--fleet", type=Path, default=Path("data/private/task2b_peak_day_fleet.csv"))
    ap.add_argument("--reference", type=Path, default=Path("data/reference"))
    ap.add_argument("--policy", choices=["throughput", "balanced", "fairness"], default="balanced")
    ap.add_argument("--time-limit", type=float, default=60.0)
    ap.add_argument("--out", type=Path, default=Path("data/private/submission_task2b.csv"))
    a = ap.parse_args(argv)
    if not a.scenarios.exists():
        print(f"{a.scenarios} not found. Copy the Datathon files into data/private/ (they are gitignored).")
        return 2
    return run(a.scenarios, a.fleet, a.reference, a.policy, a.time_limit, a.out)


if __name__ == "__main__":
    sys.exit(main())
