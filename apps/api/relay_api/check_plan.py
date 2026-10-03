"""Engine check on the demo day.

Closes Wednesday's orders in a throw-away copy of the demo workspace, plans it under every policy
with the same code path the dispatcher uses, and validates each plan with our port of the
organisers' ``check_allocation.py`` (``relay_engine.validate_allocation``). It also checks that
every order is either served or deferred with a reason, and that every deferral says whether it
was unavoidable or a choice.

Nothing is written: the copy lives inside one transaction that is rolled back at the end.

    uv run python -m relay_api.check_plan                      # all three policies
    uv run python -m relay_api.check_plan --policy fairness    # one policy
"""

from __future__ import annotations

import argparse
import secrets
import time

from relay_engine import POLICIES, POLICY_LABEL, validate_allocation

from .db import SessionLocal
from .domain.planning import close_orders, solve
from .seed import create_workspace, ensure_seeded_locked


def _row(cells: list[str], widths: list[int]) -> str:
    return "  ".join(c.ljust(w) if i == 0 else c.rjust(w) for i, (c, w) in enumerate(zip(cells, widths, strict=True)))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--policy", choices=list(POLICIES), action="append", help="repeatable; default: every policy")
    a = ap.parse_args(argv)
    policies = a.policy or list(POLICIES)

    failures: list[str] = []
    with SessionLocal() as db:
        ensure_seeded_locked(db)  # reference data + accounts on a fresh database (idempotent)
        try:
            ws = create_workspace(db, "check_" + secrets.token_hex(3), "Engine check", "CHK-" + secrets.token_hex(2).upper(), sandbox=True)
            closed = close_orders(db, ws, "check-plan")
            n_std = closed["standing_placed"]
            print(
                f"Demo day {ws.service_date:%a %d %b %Y}: {closed['total']} orders after the cutoff ({n_std} standing order{'s' if n_std != 1 else ''} placed)."
            )
            print()
            widths = [15, 9, 7, 7, 6, 14, 9, 11, 10]
            print(_row(["policy", "status", "solve", "served", "trips", "unavoid/choice", "cold m3", "repeat skip", "violations"], widths))
            print(_row(["-" * w for w in widths], widths))
            for policy in policies:
                t0 = time.perf_counter()
                sol, built = solve(db, ws, policy)
                secs = time.perf_counter() - t0
                p = built.problem
                errors = validate_allocation(p.orders, {v.id: v for v in p.vehicles}, p.districts, p.allowance, sol.assignments)

                reasons = {d.order_id: d for d in sol.deferred}
                unexplained = [o.id for o in p.orders if o.id not in sol.assignments and o.id not in reasons]
                bad_kind = [d.order_id for d in sol.deferred if d.kind not in ("unavoidable", "choice") or not d.text.strip()]
                k = sol.kpis
                unavoidable = sum(1 for d in sol.deferred if d.kind == "unavoidable")
                print(
                    _row(
                        [
                            POLICY_LABEL.get(policy, policy),
                            sol.status,
                            f"{secs:.1f}s",
                            f"{k['served']}/{k['orders']}",
                            str(k["trips"]),
                            f"{unavoidable}/{len(sol.deferred) - unavoidable}",
                            f"{k['chilled_served_m3']:.0f}/{k['chilled_m3']:.0f}",
                            f"{k['repeat_skips']}/{k['repeats_total']}",
                            str(len(errors)),
                        ],
                        widths,
                    )
                )
                for e in errors:
                    failures.append(f"{policy}: {e.rule}: {e.message}")
                if unexplained:
                    failures.append(f"{policy}: deferred without a reason: {', '.join(unexplained[:10])}")
                if bad_kind:
                    failures.append(f"{policy}: deferral without a kind or text: {', '.join(bad_kind[:10])}")
                if sol.status not in ("OPTIMAL", "FEASIBLE"):
                    failures.append(f"{policy}: solver status {sol.status}")
        finally:
            db.rollback()  # the throw-away workspace disappears with the transaction

    print()
    if failures:
        print("FAILED")
        for f in failures:
            print("  -", f)
        return 1
    print("OK: every plan passes the allocation checker, and every deferral is explained.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
