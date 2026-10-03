"""The seeded delivery day: Wednesday 30 September 2026.

Orders follow real ordering behaviour (data/demo/outlet_profiles.json, aggregated from the
history): every Fresh outlet orders dry goods daily, chilled orders come from the outlets that
order chilled on Wednesdays, Style outlets on their weekly day, Tech outlets as needed. Sizes are
drawn around each outlet's own quartiles with a fixed random seed, so every fresh install gets the
identical day.

On top of that sit the conditions the walkthrough exercises (documented in the README):
  * 5 of 9 refrigerated vehicles at Peliyagoda and 1 of 7 at Kandy are in the workshop
  * yesterday's run (Tue 29 Sep) deferred a few chilled orders; they are carried over with
    `deferred_yesterday`, and Puttalam Town has gone 5 days without a delivery
  * Kurunegala Style sent one 40.7 m3 order that is larger than any vehicle (unavoidable)
  * Fathima's outlet (Nuwara Eliya Town) has a standing dry-goods order she can edit before the
    16:00 cutoff on Tuesday; it auto-places at the cutoff if she does nothing
  * standing routes: outlets keep their usual vehicle when the plan allows (continuity), with
    the hill run on VEH057 (Sunil) and Kandy's van-only cold run on VEH058
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

SERVICE_DATE = date(2026, 9, 30)  # Wednesday
PREVIOUS_DATE = date(2026, 9, 29)
DOW = SERVICE_DATE.weekday()  # 2

WORKSHOP = {"VEH001", "VEH002", "VEH004", "VEH005", "VEH035", "VEH043"}

# Standing-route overrides (the rest comes from each outlet's usual vehicle in the history)
USUAL_OVERRIDES = {
    ("OUT104", "chilled"): "VEH057",
    ("OUT106", "chilled"): "VEH057",
    ("OUT107", "chilled"): "VEH057",
    ("OUT105", "chilled"): "VEH057",
    ("OUT076", "chilled"): "VEH058",
    ("OUT080", "chilled"): "VEH058",
    ("OUT081", "chilled"): "VEH058",
    ("OUT082", "chilled"): "VEH058",
    ("OUT083", "chilled"): "VEH058",
}

# Tuesday's deferrals, carried into Wednesday (outlet, temp, units, days since last served)
CARRY_OVER = [
    ("OUT105", "chilled", 24, 2),  # Fathima: skipped on Tuesday; first in line today
    ("OUT074", "chilled", 58, 5),  # Puttalam Town: five days without a chilled delivery
    ("OUT065", "chilled", 80, 2),  # Kurunegala Town
    ("OUT009", "chilled", 41, 2),  # Nugegoda
]

# Sizes pinned for the walkthrough (all inside each outlet's own historical range): the hill
# run's chilled orders fit Sunil's reefer van (1,040 kg) together with Fathima's carried-over order.
SIZE_OVERRIDES = {
    ("OUT104", "chilled"): 30,
    ("OUT105", "chilled"): 22,
    ("OUT106", "chilled"): 31,
    ("OUT107", "chilled"): 33,
}

# Orders that make the day interesting without breaking realism
SPECIALS = [
    ("OUT070", "ambient", 168, 0.2423, 14.6, "Seasonal stock for the festival window"),  # 40.7 m3: too big
]

# Fathima's standing order (dry goods), editable until Tuesday 16:00
STANDING = {
    "OUT105": {
        "ambient": [("FR-DRY", 19), ("FR-BEV", 10), ("FR-HSE", 5)],
    }
}


@dataclass
class DemoOrder:
    outlet_id: str
    temp: str
    units: int
    weight_kg: float
    volume_m3: float
    lines: list[tuple[str, int]] = field(default_factory=list)
    deferred_yesterday: bool = False
    days_since_served: int = 1
    channel: str = "app"
    placed_minute: int = 0  # minutes after midnight on Tuesday
    note: str = ""
    carry_over: bool = False


def load_profiles(path: Path) -> dict:
    return json.loads(path.read_text())["outlets"]


def _size(rng: random.Random, prof: dict) -> int:
    lo, mid, hi = prof["units_q25"], prof["units_med"], prof["units_q75"]
    u = rng.triangular(max(1.0, lo * 0.9), max(lo + 1, hi * 1.1), mid)
    return max(1, int(round(u)))


def build_orders(profiles: dict, outlets: dict[str, dict], catalog: dict[str, list[dict]], seed: int = 30) -> list[DemoOrder]:
    """All Wednesday orders except Fathima's standing order (placed by her or at cutoff)."""
    rng = random.Random(seed)
    out: list[DemoOrder] = []
    for oid in sorted(outlets):
        o = outlets[oid]
        prof = profiles.get(oid, {})
        for temp in ("ambient", "chilled"):
            p = prof.get(temp)
            if not p:
                continue
            prob = float(p["p_by_dow"].get(str(DOW), p["p_by_dow"].get(DOW, 0.0)))
            if o["brand"] == "Tech":
                ordered = rng.random() < prob
            else:
                ordered = prob >= 0.5
            if not ordered:
                continue
            if oid in STANDING and temp in STANDING[oid]:
                continue  # Fathima places it (or the standing order does at cutoff)
            units = SIZE_OVERRIDES.get((oid, temp)) or _size(rng, p)
            out.append(_make(oid, temp, units, p["kg_per_unit"], p["m3_per_unit"], o["brand"], catalog, rng))
    for oid, temp, units, days in CARRY_OVER:
        p = profiles[oid][temp]
        d = _make(oid, temp, units, p["kg_per_unit"], p["m3_per_unit"], outlets[oid]["brand"], catalog, rng)
        d.deferred_yesterday, d.days_since_served, d.carry_over = True, days, True
        d.channel = "carryover"
        d.placed_minute = 15 * 60 + 10  # placed for Tuesday, carried at Tuesday's plan
        d.note = "Deferred on Tue 29 Sep · carried over"
        out.append(d)
    for oid, temp, units, m3u, kgu, note in SPECIALS:
        d = _make(oid, temp, units, kgu, m3u, outlets[oid]["brand"], catalog, rng)
        d.note = note
        out.append(d)
    # placement times and channels (Tuesday 08:00-15:40, ~30% by phone)
    for d in out:
        if not d.placed_minute:
            d.placed_minute = rng.randint(8 * 60, 15 * 60 + 40)
            d.channel = "phone" if rng.random() < 0.3 else "app"
    return out


def standing_order(outlet_id: str, temp: str, catalog: dict[str, list[dict]]) -> DemoOrder | None:
    spec = STANDING.get(outlet_id, {}).get(temp)
    if not spec:
        return None
    by_sku = {p["sku"]: p for ps in catalog.values() for p in ps}
    lines = [(sku, qty) for sku, qty in spec]
    units = sum(q for _, q in lines)
    kg = sum(by_sku[s]["unit_kg"] * q for s, q in lines)
    m3 = sum(by_sku[s]["unit_m3"] * q for s, q in lines)
    return DemoOrder(outlet_id, temp, units, round(kg, 1), round(m3, 3), lines, channel="standing")


def _make(
    oid: str, temp: str, units: int, kgu: float, m3u: float, brand: str, catalog: dict[str, list[dict]], rng: random.Random
) -> DemoOrder:
    products = catalog[_cat_key(brand, temp)]
    lines = _split_lines(units, products, rng)
    return DemoOrder(oid, temp, units, round(units * kgu, 1), round(units * m3u, 3), lines)


def _cat_key(brand: str, temp: str) -> str:
    if brand == "Fresh":
        return "Fresh:" + temp
    return brand + ":ambient"


def _split_lines(units: int, products: list[dict], rng: random.Random) -> list[tuple[str, int]]:
    weights = [p["share"] for p in products]
    left = units
    lines = []
    for i, p in enumerate(products):
        if i == len(products) - 1:
            q = left
        else:
            q = max(1 if units >= len(products) else 0, int(round(units * weights[i] * rng.uniform(0.85, 1.15))))
            q = min(q, left - (len(products) - 1 - i))
        q = max(0, q)
        left -= q
        if q:
            lines.append((p["sku"], q))
    return lines
