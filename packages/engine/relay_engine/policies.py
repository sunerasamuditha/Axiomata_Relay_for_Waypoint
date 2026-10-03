"""Deferral policies: the three positions of the dispatcher's deferral lever.

Each policy is a set of weights for one CP-SAT objective. The weights are spaced so that the
priority tiers act lexicographically (a higher tier always beats any amount of a lower one):

  tier 1  fairness      outlets deferred on the previous run (never skip twice)
  tier 2  priority      chilled Fresh > ambient Fresh > Tech > Style, scaled by days since served
  tier 3  volume        more cubic metres served
  tier 4  efficiency    fewer trips, fewer kilometres, keep outlets on their usual vehicle

  throughput  "Serve the most orders": every order is worth the same large amount; priority and
              repeat skips only break ties.
  balanced    "Protect cheap repeats": a repeat skip is worth about one and a half ordinary
              orders, so the planner will displace one small order to end a repeat skip but not two.
  fairness    "No outlet skipped twice": a repeat skip dominates everything; the planner serves
              every repeat it can, whatever it costs.
"""

from __future__ import annotations

from .core import Order

CLASS_VALUE = {("Fresh", "chilled"): 100, ("Fresh", "ambient"): 80, ("Tech", "ambient"): 60, ("Style", "ambient"): 50}

POLICY_LABEL = {"throughput": "Max throughput", "balanced": "Balanced", "fairness": "Fairness first"}
POLICY_SUB = {
    "throughput": "Serve the most orders",
    "balanced": "Protect cheap repeats",
    "fairness": "No outlet skipped twice",
}

TRIP_COST = 40  # per trip slot used
KM_COST = 1  # per km driven (return leg included)
CONTINUITY_BONUS = 6  # keep an outlet on the vehicle and driver that usually serve it
SECOND_FRESH_TRIP_COST = 150  # a vehicle's second pre-dawn trip tends to run late


def class_value(o: Order) -> int:
    return CLASS_VALUE.get((o.brand, o.temp), 50)


def urgency(o: Order) -> float:
    """1.0 for an outlet served yesterday, rising 0.2 per extra day without service (max 2.0)."""
    return 1.0 + 0.2 * min(max(o.days_since_served - 1, 0), 5)


def order_score(o: Order, policy: str) -> int:
    base = class_value(o) * urgency(o)
    vol = min(o.volume_m3, 40.0)
    repeat = 1 if o.deferred_yesterday else 0
    if policy == "throughput":
        return int(10_000 + base + 50 * repeat + 2 * vol)
    if policy == "fairness":
        return int(1_000 + 10 * base + 1_000_000 * repeat + 5 * vol)
    # balanced
    return int(2_000 + 20 * base + 3_000 * repeat + 5 * vol)
