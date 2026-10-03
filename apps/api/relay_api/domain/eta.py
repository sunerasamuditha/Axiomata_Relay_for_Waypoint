"""Predictions for planned stops (service minutes, lateness probability) and live ETAs."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta

from relay_ml import StopFeatures, get_predictor, monsoon_for_month
from sqlalchemy.orm import Session

from ..models import District, Order, Outlet, ServiceAllowance, TrafficSpeed, Trip, Vehicle
from .clock import minutes_of


class RefCache:
    """Reference tables are small and immutable at runtime: load once per process."""

    _loaded = False
    outlets: dict[str, Outlet] = {}
    vehicles: dict[str, Vehicle] = {}
    districts: dict[str, District] = {}
    allowance: dict[tuple[str, str], int] = {}
    speed: dict[tuple[str, int, int], float] = {}

    _lock = threading.Lock()

    @classmethod
    def load(cls, db: Session, force: bool = False) -> type[RefCache]:
        if cls._loaded and not force:
            return cls
        with cls._lock:  # requests run in a thread pool: build privately, then swap in
            if cls._loaded and not force:
                return cls
            outlets = {o.id: o for o in db.query(Outlet)}
            vehicles = {v.id: v for v in db.query(Vehicle)}
            districts = {d.name: d for d in db.query(District)}
            allowance = {(a.brand, a.dock_type): a.minutes for a in db.query(ServiceAllowance)}
            speed = {(t.district, t.hour, t.monsoon): t.speed_index for t in db.query(TrafficSpeed)}
            for obj in [*outlets.values(), *vehicles.values(), *districts.values()]:
                db.expunge(obj)
            cls.outlets, cls.vehicles, cls.districts, cls.allowance, cls.speed = outlets, vehicles, districts, allowance, speed
            cls._loaded = bool(outlets)
        return cls


def predict_trip(db: Session, trip: Trip, orders: dict[int, Order]) -> None:
    """Fill pred_service_min and late_prob on a trip's stops from the ML model (or heuristic)."""
    ref = RefCache.load(db)
    v = ref.vehicles[trip.vehicle_id]
    d = ref.districts[trip.district]
    stops = sorted(trip.stops, key=lambda s: s.seq)
    day = trip.service_date
    monsoon = monsoon_for_month(day.month)
    rows = []
    for i, s in enumerate(stops):
        o = orders[s.order_id]
        ou = ref.outlets[o.outlet_id]
        arrive = minutes_of(s.planned_arrival, day)
        hour = (arrive // 60) % 24
        open_ = _hm(ou.window_open)
        close = _hm(ou.window_close)
        rows.append(
            StopFeatures(
                brand=o.brand,
                district=trip.district,
                dock_type=ou.dock_type,
                parking=ou.parking,
                temp=o.temp,
                vehicle_type=v.type,
                vehicle_temp=v.temp,
                units=o.units,
                weight_kg=o.weight_kg,
                volume_m3=o.volume_m3,
                seq=i,
                n_stops=len(stops),
                planned_arrival=arrive,
                window_open=open_,
                window_close=close,
                leg_km=d.d2d_km if i == 0 else d.inter_km,
                leg_min=d.d2d_min if i == 0 else d.inter_min,
                cum_planned_min=arrive - minutes_of(trip.planned_depart, day),
                dow=day.weekday(),
                monsoon=monsoon,
                speed_index=ref.speed.get((trip.district, hour, monsoon), 100.0),
                disruption_index=100.0,
                allowance=ref.allowance.get((o.brand, ou.dock_type), 15),
            )
        )
    for s, (svc, late) in zip(stops, get_predictor().predict(rows), strict=False):
        s.pred_service_min = svc
        s.late_prob = late


def _hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def live_etas(trip: Trip, now: datetime) -> dict[int, dict]:
    """Expected arrival for every stop of a trip, given what has actually happened so far.

    Stops already done keep their actual times. The next pending stop is expected at
    max(planned arrival, previous actual finish + inter-stop travel); delays propagate down the
    run. While a vehicle is dark the same projection is labelled an estimate."""
    ref = RefCache
    d = ref.districts.get(trip.district)
    inter = d.inter_min if d else 10
    out: dict[int, dict] = {}
    shift = timedelta(0)
    prev_finish: datetime | None = None
    started = trip.departed_at
    if started and trip.planned_depart:
        shift = max(timedelta(0), started - trip.planned_depart)
    stops = sorted(trip.stops, key=lambda s: s.seq)
    for i, s in enumerate(stops):
        if s.completed_at:
            out[s.id] = {"eta": (s.arrived_at or s.completed_at).isoformat(), "kind": "actual"}
            prev_finish = s.completed_at
            shift = max(timedelta(0), s.completed_at - s.planned_finish)
            continue
        if s.arrived_at:
            out[s.id] = {"eta": s.arrived_at.isoformat(), "kind": "actual"}
            prev_finish = s.arrived_at + timedelta(minutes=s.pred_service_min or 15)
            shift = max(timedelta(0), prev_finish - s.planned_finish)
            continue
        eta = s.planned_arrival + shift
        if prev_finish is not None and i > 0 and stops[i - 1].order.outlet_id != s.order.outlet_id:
            eta = max(eta, prev_finish + timedelta(minutes=inter))
        elif prev_finish is not None:
            eta = max(eta, prev_finish)
        if trip.status == "out" and eta < now and not s.arrived_at:
            eta = now + timedelta(minutes=2)  # it cannot have arrived in the past without telling us
        band_lo = eta - timedelta(minutes=4)
        band_hi = eta + timedelta(minutes=6 + round((s.late_prob or 0.1) * 38))
        out[s.id] = {
            "eta": eta.isoformat(),
            "kind": "estimate" if trip.signal == "dark" else "predicted",
            "band_lo": band_lo.isoformat(),
            "band_hi": band_hi.isoformat(),
        }
        prev_finish = eta + timedelta(minutes=s.pred_service_min or 15)
    return out
