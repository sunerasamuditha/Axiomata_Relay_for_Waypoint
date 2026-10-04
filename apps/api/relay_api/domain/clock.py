"""The workspace's virtual clock.

The demo day happens at real times of day (orders close Tuesday 16:00, the hill van leaves at
03:05), but judges can walk through it at their own pace. Each workspace therefore has its own clock:

  * it starts paused at Tuesday 29 September 15:20, forty minutes before the order cutoff
  * people's actions move it forward to the moment they would really happen ("snap forward"):
    starting to load a truck, releasing it, starting a run, arriving at a stop, delivering
  * the dispatcher's demo controls can add minutes, or let it run at 1x, 10x or 60x
  * it never moves backwards, and every event records the virtual time it happened
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from ..models import Workspace

SEED_VIRTUAL_START = datetime(2026, 9, 29, 15, 20)
CUTOFF_TIME = time(16, 0)


def utcnow() -> datetime:
    return datetime.now(UTC)


def now_virtual(ws: Workspace, real_now: datetime | None = None) -> datetime:
    if ws.clock_paused:
        return ws.clock_anchor_virtual
    real_now = real_now or utcnow()
    anchor = ws.clock_anchor_real if ws.clock_anchor_real.tzinfo else ws.clock_anchor_real.replace(tzinfo=UTC)
    elapsed = max(0.0, (real_now - anchor).total_seconds()) * (ws.clock_rate or 1.0)
    return ws.clock_anchor_virtual + timedelta(seconds=elapsed)


def set_virtual(ws: Workspace, at: datetime) -> None:
    ws.clock_anchor_virtual = at.replace(microsecond=0)
    ws.clock_anchor_real = utcnow()


def advance_to(ws: Workspace, at: datetime | None) -> bool:
    """Snap the clock forward to `at` if it is in the future. Returns True if it moved."""
    if at is None:
        return False
    now = now_virtual(ws)
    if at > now:
        set_virtual(ws, at)
        return True
    return False


def advance_by(ws: Workspace, minutes: float) -> None:
    set_virtual(ws, now_virtual(ws) + timedelta(minutes=minutes))


def set_running(ws: Workspace, rate: float | None) -> None:
    """rate None or 0 pauses; otherwise the clock runs at `rate` x real time."""
    now = now_virtual(ws)
    ws.clock_anchor_virtual = now
    ws.clock_anchor_real = utcnow()
    if not rate:
        ws.clock_paused = True
    else:
        ws.clock_paused = False
        ws.clock_rate = float(rate)


def cutoff_for(service_date: date) -> datetime:
    """Orders for a delivery day close at 16:00 the previous day."""
    return datetime.combine(service_date - timedelta(days=1), CUTOFF_TIME)


def at_minutes(day: date, minutes: int | float) -> datetime:
    return datetime.combine(day, time(0, 0)) + timedelta(minutes=float(minutes))


def minutes_of(dt: datetime, day: date) -> int:
    return int(round((dt - datetime.combine(day, time(0, 0))).total_seconds() / 60))


def clock_payload(ws: Workspace) -> dict:
    now = now_virtual(ws)
    return {
        "now": now.isoformat(),
        "anchor_virtual": ws.clock_anchor_virtual.isoformat(),
        "anchor_real": (ws.clock_anchor_real if ws.clock_anchor_real.tzinfo else ws.clock_anchor_real.replace(tzinfo=UTC)).isoformat(),
        "rate": ws.clock_rate,
        "paused": ws.clock_paused,
        "service_date": ws.service_date.isoformat(),
        "cutoff": cutoff_for(ws.service_date).isoformat(),
        "orders_closed_at": ws.orders_closed_at.isoformat() if ws.orders_closed_at else None,
        "workspace": ws.id,
        "workspace_code": ws.code,
        "sandbox": ws.sandbox,
    }
