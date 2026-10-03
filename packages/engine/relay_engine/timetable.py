"""Stage 2: sequence the stops of every trip and give each vehicle a timetable.

Stage 1 guarantees the published planning standard (capacity, access, the 270/480 minute budgets,
fuel). Stage 2 turns each vehicle's trips into clock times that respect delivery windows as well
as the plan allows:

  * trips run Fresh first (pre-dawn), then Style/Tech (daytime); within a group, the trip with the
    earliest-closing window goes first
  * stops are ordered earliest-deadline-first (mall bays by their mall window)
  * a vehicle that arrives before a window opens waits (the booklet's rule); service starts at
    max(arrival, open)
  * the departure is chosen as late as possible while still reaching every stop before it
    closes, but never before 03:30 for Fresh (the booklet's pre-dawn operating window is
    03:30-08:00), 06:00 for daytime trips, or before the vehicle is back and reloaded from its
    previous trip
  * any stop still planned after its window closes is flagged `late` so the dispatcher sees the
    risk before publishing (the ML model then adds a predicted lateness probability on top)
"""

from __future__ import annotations

from .core import District, Order, StopTime, TripPlan

EARLIEST_FRESH = 3 * 60 + 30  # 03:30, start of the Fresh operating window
EARLIEST_DAY = 6 * 60  # 06:00
RELOAD_MIN = 20


def stop_window(o: Order) -> tuple[int, int]:
    if o.parking == "mall_dock" and o.mall_window:
        return o.mall_window
    return (o.window_open, o.window_close)


def sequence(orders: list[Order]) -> list[Order]:
    return sorted(orders, key=lambda o: (stop_window(o)[1], stop_window(o)[0], o.outlet_id, o.id))


def simulate(
    depart: int,
    orders: list[Order],
    d: District,
    allowance: dict[tuple[str, str], int],
    service: dict[str, float] | None = None,
) -> tuple[list[StopTime], int]:
    t = depart + d.d2d_min
    out: list[StopTime] = []
    for i, o in enumerate(orders):
        if i and o.outlet_id != orders[i - 1].outlet_id:  # two orders for one outlet: one visit
            t += d.inter_min
        op, cl = stop_window(o)
        arrive = t
        start = max(arrive, op)
        svc = (service or {}).get(o.id) or allowance[(o.brand, o.dock_type)]
        finish = int(round(start + svc))
        out.append(StopTime(o.id, int(arrive), int(start), finish, arrive > cl))
        t = finish
    return out, t + d.d2d_min


def best_departure(
    orders: list[Order],
    d: District,
    allowance: dict[tuple[str, str], int],
    earliest: int,
    service: dict[str, float] | None = None,
) -> int:
    """Latest departure (5-minute grid) that reaches every stop before it closes, else earliest."""
    if not orders:
        return earliest
    first_open = stop_window(orders[0])[0]
    dep = max(earliest, first_open - d.d2d_min)
    for _ in range(4):
        stops, _ = simulate(dep, orders, d, allowance, service)
        over = max((s.arrive - stop_window(o)[1] for s, o in zip(stops, orders, strict=False)), default=0)
        if over <= 0:
            break
        dep = max(earliest, dep - over)
    return max(earliest, (dep // 5) * 5)


def _lateness(stops: list[StopTime], orders: list[Order]) -> int:
    return sum(max(0, st.arrive - stop_window(o)[1]) for st, o in zip(stops, orders, strict=False))


def _run_chain(chain, orders, districts, allowance, service, first_departure=None):
    """Timetable trips in order; returns (total late minutes, waiting minutes, plans)."""
    free_at = 0
    late = wait = 0
    plans = []
    for i, t in enumerate(chain):
        d = districts[t.district]
        seq = sequence([orders[oid] for oid in t.order_ids])
        base = EARLIEST_FRESH if t.brand == "Fresh" else EARLIEST_DAY
        earliest = max(base, free_at + (RELOAD_MIN if free_at else 0))
        if i == 0 and first_departure is not None:
            dep = max(earliest, first_departure)
        else:
            dep = best_departure(seq, d, allowance, earliest, service)
        stops, ret = simulate(dep, seq, d, allowance, service)
        late += _lateness(stops, seq)
        wait += sum(st.start - st.arrive for st in stops)
        plans.append((t, seq, dep, ret, stops))
        free_at = ret
    return late, wait, plans


def timetable_vehicle(
    trips: list[TripPlan],
    orders: dict[str, Order],
    districts: dict[str, District],
    allowance: dict[tuple[str, str], int],
    service: dict[str, float] | None = None,
) -> list[TripPlan]:
    """Order a vehicle's trips, renumber them 1..n, sequence stops and assign clock times.

    With two Fresh trips the first must leave early enough for the second to make its windows, so
    both trip orders and every first departure from 03:30 (10-minute steps) are tried, and the
    timetable with the least lateness (then the least waiting) wins.
    """
    fresh = [t for t in trips if t.brand == "Fresh"]
    day = sorted(
        (t for t in trips if t.brand != "Fresh"), key=lambda t: min((stop_window(orders[o])[1] for o in t.order_ids), default=1440)
    )
    candidates = []
    if len(fresh) <= 1:
        candidates.append(_run_chain(fresh + day, orders, districts, allowance, service))
    else:
        for chain_fresh in (fresh, list(reversed(fresh))):
            chain = chain_fresh + day
            t1 = chain[0]
            latest = best_departure(sequence([orders[o] for o in t1.order_ids]), districts[t1.district], allowance, EARLIEST_FRESH, service)
            for dep in range(EARLIEST_FRESH, latest + 1, 10):
                candidates.append(_run_chain(chain, orders, districts, allowance, service, dep))
            candidates.append(_run_chain(chain, orders, districts, allowance, service, latest))
    late, wait, plans = min(candidates, key=lambda c: (c[0], c[1]))
    out = []
    for i, (t, seq, dep, ret, stops) in enumerate(plans, start=1):
        t.trip_no = i
        t.order_ids = [o.id for o in seq]
        t.depart, t.return_at, t.stops = dep, ret, stops
        out.append(t)
    return out
