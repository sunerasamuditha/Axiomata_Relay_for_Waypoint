"""The rest of the network, simulated.

Judges drive one van (VEH057), load at one dock and receive at one store. The other trips of the
published plan still have to load, leave, deliver and be received, or the dispatcher's canvas
would be a still life. This module moves them along the workspace's virtual clock:

  depart - 75 min   loading          depart - 15 min   loaded (sealed by the dock crew)
  depart            on the road      planned arrival   arrived (+ a delay drawn from the stop's
                                                       predicted late probability)
  + service time    delivered (proof recorded, marked simulated)
  + 30-50 min       received by the store (except the judges' store)

It is deterministic (seeded by stop id), idempotent and safe to run from several processes: a
Postgres advisory lock lets one runner work on a workspace at a time.
"""

from __future__ import annotations

import hashlib
import random
from datetime import timedelta

from sqlalchemy.orm import Session

from ..models import Proof, Receipt, Trip, User, Workspace
from .clock import now_virtual
from .events import notify
from .fieldops import detect_dark
from .planning import published_plan


def _rng(*parts) -> random.Random:
    h = hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()
    return random.Random(int(h[:12], 16))


def advance(db: Session, ws: Workspace) -> bool:
    """Move simulated trips up to the clock. Returns True if anything changed."""
    from ..db import advisory_lock

    if not advisory_lock(db, int(hashlib.sha1(ws.id.encode()).hexdigest()[:8], 16)):
        return False
    changed = detect_dark(db, ws)
    plan = published_plan(db, ws)
    if not plan:
        return changed
    now = now_virtual(ws)
    judge_outlets = {u.outlet_id for u in db.query(User).filter(User.role == "store", User.outlet_id.isnot(None))}
    topics: set[str] = set()
    for trip in db.query(Trip).filter_by(plan_id=plan.id).all():
        if trip.controlled != "sim" or trip.status == "done":
            if trip.controlled == "human":
                changed |= _auto_receipts(db, ws, trip, now, judge_outlets, topics)
            continue
        dep = trip.planned_depart
        before = trip.status
        if trip.status == "planned" and now >= dep - timedelta(minutes=75):
            trip.status, trip.loading_started_at = "loading", dep - timedelta(minutes=75)
        if trip.status == "loading" and now >= dep - timedelta(minutes=15):
            trip.status, trip.released_at, trip.released_by = "loaded", dep - timedelta(minutes=15), "Dock crew"
            for s in trip.stops:
                for ln in s.order.lines:
                    if ln.load_state == "todo":
                        ln.load_state, ln.loaded_qty, ln.loaded_by, ln.loaded_at = "loaded", ln.qty, "Dock crew", trip.released_at
                s.order.status = "loaded"
        if trip.status == "loaded" and now >= dep:
            trip.status, trip.departed_at, trip.last_contact_at = "out", dep, dep
            for s in trip.stops:
                s.order.status = "out"
        if trip.status == "out":
            t = trip.departed_at or dep
            for s in sorted(trip.stops, key=lambda x: x.seq):
                if s.status in ("delivered", "partial", "failed"):
                    t = s.completed_at
                    continue
                r = _rng(ws.id, s.id)
                delay = r.uniform(12, 35) if r.random() < (s.late_prob or 0.1) else r.uniform(-6, 9)
                arrive = max(s.planned_arrival + timedelta(minutes=delay), t)
                if now < arrive:
                    break
                s.arrived_at = arrive
                s.status = "arrived"
                finish = arrive + timedelta(minutes=max(6.0, (s.pred_service_min or 15) + r.uniform(-3, 4)))
                if now < finish:
                    break
                s.status, s.completed_at, s.simulated = "delivered", finish, True
                s.order.status = "delivered"
                for ln in s.order.lines:
                    ln.delivered_qty = ln.loaded_qty if ln.loaded_qty is not None else ln.qty
                db.add(Proof(workspace_id=ws.id, stop_id=s.id, receiver_name="Store staff", captured_at=finish, simulated=True))
                topics.add(f"outlet:{s.order.outlet_id}")
                t = finish
            trip.last_contact_at = now
            if all(s.status in ("delivered", "partial", "failed") for s in trip.stops):
                trip.status, trip.completed_at = "done", t
        changed |= _auto_receipts(db, ws, trip, now, judge_outlets, topics)
        if trip.status != before:
            changed = True
            topics.add(f"depot:{trip.depot}")
    if changed or topics:
        notify(db, ws.id, ["dispatch", *topics], kind="sim")
    return changed


def _auto_receipts(db: Session, ws: Workspace, trip: Trip, now, judge_outlets: set[str], topics: set[str]) -> bool:
    changed = False
    for s in trip.stops:
        o = s.order
        if s.status != "delivered" or o.outlet_id in judge_outlets or o.status == "received":
            continue
        r = _rng("rcpt", ws.id, s.id)
        if now >= s.completed_at + timedelta(minutes=r.uniform(30, 50)):
            if not db.query(Receipt).filter_by(order_id=o.id).first():
                db.add(
                    Receipt(
                        workspace_id=ws.id,
                        order_id=o.id,
                        by_name="Store staff",
                        at=s.completed_at + timedelta(minutes=35),
                        status="confirmed",
                        simulated=True,
                    )
                )
                for ln in o.lines:
                    ln.received_qty = ln.delivered_qty
            o.status = "received"
            topics.add(f"outlet:{o.outlet_id}")
            changed = True
    return changed
