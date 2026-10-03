"""Relational model.

Reference data (the shared datasets) is global and read-only. Everything operational carries a
`workspace_id`, so judges can work in private sandboxes and the demo can be reset without touching
anyone else's day. The chain every role shares is

    Order ─< OrderLine
      │
      └── Stop >── Trip >── Plan          (a stop is one order on one vehicle trip)
            │
            ├── Proof                      (driver)
            └── Receipt                    (store)

and every change is appended to `event` (the audit log, the idempotency key store for offline
sync, and the source of live updates).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

TZ = DateTime(timezone=True)

# ------------------------------------------------------------------------------------------------
# workspaces and people
# ------------------------------------------------------------------------------------------------


class Workspace(Base):
    __tablename__ = "workspace"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    code: Mapped[str] = mapped_column(String(16), unique=True)
    created_at: Mapped[datetime] = mapped_column(TZ)
    # virtual clock: now = anchor_virtual + (real_now - anchor_real) * rate, unless paused
    clock_anchor_real: Mapped[datetime] = mapped_column(TZ)
    clock_anchor_virtual: Mapped[datetime] = mapped_column(DateTime)
    clock_rate: Mapped[float] = mapped_column(Float, default=1.0)
    clock_paused: Mapped[bool] = mapped_column(Boolean, default=True)
    service_date: Mapped[date] = mapped_column(Date)
    orders_closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    seed_version: Mapped[int] = mapped_column(Integer, default=1)
    sandbox: Mapped[bool] = mapped_column(Boolean, default=False)


class User(Base):
    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(120), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    short_name: Mapped[str] = mapped_column(String(40))
    initials: Mapped[str] = mapped_column(String(4))
    color: Mapped[str] = mapped_column(String(16), default="#1F4BFF")
    role: Mapped[str] = mapped_column(String(16))  # dispatcher | loader | driver | store
    title: Mapped[str] = mapped_column(String(80), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    depot: Mapped[str | None] = mapped_column(String(20), nullable=True)
    dock: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vehicle_id: Mapped[str | None] = mapped_column(String(10), nullable=True)
    outlet_id: Mapped[str | None] = mapped_column(String(10), nullable=True)
    lang: Mapped[str] = mapped_column(String(4), default="en")


# ------------------------------------------------------------------------------------------------
# reference data (shared datasets)
# ------------------------------------------------------------------------------------------------


class Depot(Base):
    __tablename__ = "depot"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)  # Peliyagoda | Kandy
    code: Mapped[str] = mapped_column(String(4))  # PEL | KDY
    name: Mapped[str] = mapped_column(String(60))
    kind: Mapped[str] = mapped_column(String(40))
    docks: Mapped[int] = mapped_column(Integer, default=1)
    bays_per_dock: Mapped[int] = mapped_column(Integer, default=5)


class District(Base):
    __tablename__ = "district"

    name: Mapped[str] = mapped_column(String(30), primary_key=True)
    depot: Mapped[str] = mapped_column(ForeignKey("depot.id"))
    road_class: Mapped[str] = mapped_column(String(20))
    free_flow_kmh: Mapped[float] = mapped_column(Float)
    d2d_km: Mapped[float] = mapped_column(Float)
    d2d_min: Mapped[int] = mapped_column(Integer)
    inter_km: Mapped[float] = mapped_column(Float)
    inter_min: Mapped[int] = mapped_column(Integer)


class Outlet(Base):
    __tablename__ = "outlet"

    id: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    brand: Mapped[str] = mapped_column(String(10))
    district: Mapped[str] = mapped_column(ForeignKey("district.name"))
    depot: Mapped[str] = mapped_column(ForeignKey("depot.id"))
    dock_type: Mapped[str] = mapped_column(String(12))
    parking: Mapped[str] = mapped_column(String(12))
    mall_window: Mapped[str | None] = mapped_column(String(12), nullable=True)
    window_open: Mapped[str] = mapped_column(String(5))
    window_close: Mapped[str] = mapped_column(String(5))
    usual_vehicle_ambient: Mapped[str | None] = mapped_column(String(10), nullable=True)
    usual_vehicle_chilled: Mapped[str | None] = mapped_column(String(10), nullable=True)
    manager: Mapped[str] = mapped_column(String(60), default="")


class Vehicle(Base):
    __tablename__ = "vehicle"

    id: Mapped[str] = mapped_column(String(10), primary_key=True)
    type: Mapped[str] = mapped_column(String(8))
    temp: Mapped[str] = mapped_column(String(8))
    weight_cap_kg: Mapped[float] = mapped_column(Float)
    volume_cap_m3: Mapped[float] = mapped_column(Float)
    fuel_type: Mapped[str] = mapped_column(String(10))
    km_per_l: Mapped[float] = mapped_column(Float)
    weekly_fuel_quota_l: Mapped[float] = mapped_column(Float)
    depot: Mapped[str] = mapped_column(ForeignKey("depot.id"))
    driver_name: Mapped[str] = mapped_column(String(60), default="")


class ServiceAllowance(Base):
    __tablename__ = "service_allowance"

    brand: Mapped[str] = mapped_column(String(10), primary_key=True)
    dock_type: Mapped[str] = mapped_column(String(12), primary_key=True)
    minutes: Mapped[int] = mapped_column(Integer)


class CalendarDay(Base):
    __tablename__ = "calendar_day"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    dow: Mapped[int] = mapped_column(Integer)
    is_payday: Mapped[bool] = mapped_column(Boolean)
    festival: Mapped[str | None] = mapped_column(String(30), nullable=True)
    festival_ramp: Mapped[float] = mapped_column(Float)
    is_holiday: Mapped[bool] = mapped_column(Boolean)
    monsoon: Mapped[bool] = mapped_column(Boolean)
    is_operating: Mapped[bool] = mapped_column(Boolean)
    iso_year: Mapped[int] = mapped_column(Integer)
    iso_week: Mapped[int] = mapped_column(Integer)


class TrafficSpeed(Base):
    __tablename__ = "traffic_speed"

    district: Mapped[str] = mapped_column(String(30), primary_key=True)
    hour: Mapped[int] = mapped_column(Integer, primary_key=True)
    monsoon: Mapped[int] = mapped_column(Integer, primary_key=True)
    speed_index: Mapped[float] = mapped_column(Float)


class Product(Base):
    __tablename__ = "product"

    sku: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(40))
    brand: Mapped[str] = mapped_column(String(10))
    temp: Mapped[str] = mapped_column(String(8))
    unit_kg: Mapped[float] = mapped_column(Float)
    unit_m3: Mapped[float] = mapped_column(Float)
    uom: Mapped[str] = mapped_column(String(12))
    share: Mapped[float] = mapped_column(Float, default=0.0)
    sort: Mapped[int] = mapped_column(Integer, default=0)


# ------------------------------------------------------------------------------------------------
# operational (workspace-scoped)
# ------------------------------------------------------------------------------------------------


class VehicleDay(Base):
    __tablename__ = "vehicle_day"

    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    vehicle_id: Mapped[str] = mapped_column(ForeignKey("vehicle.id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="available")  # available | in_workshop
    fuel_used_l: Mapped[float] = mapped_column(Float, default=0.0)  # this week, before this day
    back_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str] = mapped_column(String(120), default="")


class StandingOrder(Base):
    __tablename__ = "standing_order"

    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), primary_key=True)
    outlet_id: Mapped[str] = mapped_column(ForeignKey("outlet.id"), primary_key=True)
    temp: Mapped[str] = mapped_column(String(8), primary_key=True)
    lines: Mapped[list] = mapped_column(JSON)  # [[sku, qty], ...]


class Order(Base):
    __tablename__ = "order_"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    ref: Mapped[str] = mapped_column(String(16))
    outlet_id: Mapped[str] = mapped_column(ForeignKey("outlet.id"))
    service_date: Mapped[date] = mapped_column(Date)
    brand: Mapped[str] = mapped_column(String(10))
    temp: Mapped[str] = mapped_column(String(8))
    units: Mapped[int] = mapped_column(Integer)
    weight_kg: Mapped[float] = mapped_column(Float)
    volume_m3: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="placed")
    channel: Mapped[str] = mapped_column(String(12), default="app")
    placed_at: Mapped[datetime] = mapped_column(DateTime)
    placed_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"), nullable=True)
    deferred_yesterday: Mapped[bool] = mapped_column(Boolean, default=False)
    days_since_served: Mapped[int] = mapped_column(Integer, default=1)
    deferral_streak: Mapped[int] = mapped_column(Integer, default=0)
    origin_order_id: Mapped[int | None] = mapped_column(ForeignKey("order_.id"), nullable=True)
    note: Mapped[str] = mapped_column(String(160), default="")

    lines: Mapped[list[OrderLine]] = relationship(back_populates="order", order_by="OrderLine.id", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("workspace_id", "ref"),
        Index("ix_order_ws_date", "workspace_id", "service_date"),
        Index("ix_order_ws_outlet", "workspace_id", "outlet_id"),
    )


class OrderLine(Base):
    __tablename__ = "order_line"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("order_.id", ondelete="CASCADE"), index=True)
    sku: Mapped[str] = mapped_column(ForeignKey("product.sku"))
    name: Mapped[str] = mapped_column(String(40))
    qty: Mapped[int] = mapped_column(Integer)
    uom: Mapped[str] = mapped_column(String(12))
    load_state: Mapped[str] = mapped_column(String(10), default="todo")  # todo | loaded | short | damaged
    loaded_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    loaded_by: Mapped[str | None] = mapped_column(String(60), nullable=True)
    loaded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    delivered_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    received_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    receipt_issue: Mapped[str | None] = mapped_column(String(16), nullable=True)  # damaged | missing | wrong
    receipt_note: Mapped[str | None] = mapped_column(String(200), nullable=True)

    order: Mapped[Order] = relationship(back_populates="lines")


class Plan(Base):
    __tablename__ = "plan"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    service_date: Mapped[date] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer)
    policy: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(12), default="draft")  # draft | published | superseded
    created_at: Mapped[datetime] = mapped_column(DateTime)
    created_by: Mapped[str] = mapped_column(String(60), default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    solver: Mapped[dict] = mapped_column(JSON, default=dict)
    kpis: Mapped[dict] = mapped_column(JSON, default=dict)
    lever_cache: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(String(200), default="")


class Trip(Base):
    __tablename__ = "trip"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plan.id", ondelete="CASCADE"), index=True)
    service_date: Mapped[date] = mapped_column(Date)
    code: Mapped[str] = mapped_column(String(8))
    vehicle_id: Mapped[str] = mapped_column(ForeignKey("vehicle.id"))
    trip_no: Mapped[int] = mapped_column(Integer)
    depot: Mapped[str] = mapped_column(String(20))
    brand: Mapped[str] = mapped_column(String(10))
    district: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(10), default="planned")  # planned|loading|loaded|out|done
    planned_depart: Mapped[datetime] = mapped_column(DateTime)
    planned_return: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    minutes: Mapped[int] = mapped_column(Integer, default=0)
    km: Mapped[float] = mapped_column(Float, default=0.0)
    litres: Mapped[float] = mapped_column(Float, default=0.0)
    volume_m3: Mapped[float] = mapped_column(Float, default=0.0)
    weight_kg: Mapped[float] = mapped_column(Float, default=0.0)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    dock: Mapped[int] = mapped_column(Integer, default=1)
    bay: Mapped[int] = mapped_column(Integer, default=1)
    controlled: Mapped[str] = mapped_column(String(6), default="sim")  # sim | human
    loading_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    released_by: Mapped[str | None] = mapped_column(String(60), nullable=True)
    release_temp_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    seal_no: Mapped[str | None] = mapped_column(String(20), nullable=True)
    doors_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    departed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_contact_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    signal: Mapped[str] = mapped_column(String(6), default="ok")  # ok | dark
    dark_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    stops: Mapped[list[Stop]] = relationship(back_populates="trip", order_by="Stop.seq", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_trip_ws_date", "workspace_id", "service_date"),)


class Stop(Base):
    __tablename__ = "stop"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("trip.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("order_.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    planned_arrival: Mapped[datetime] = mapped_column(DateTime)
    planned_start: Mapped[datetime] = mapped_column(DateTime)
    planned_finish: Mapped[datetime] = mapped_column(DateTime)
    late_planned: Mapped[bool] = mapped_column(Boolean, default=False)
    pred_service_min: Mapped[float] = mapped_column(Float, default=15.0)
    late_prob: Mapped[float] = mapped_column(Float, default=0.1)
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending|arrived|delivered|partial|failed
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(TZ, nullable=True)
    recorded_offline: Mapped[bool] = mapped_column(Boolean, default=False)
    outcome_note: Mapped[str] = mapped_column(String(200), default="")
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)

    trip: Mapped[Trip] = relationship(back_populates="stops")
    order: Mapped[Order] = relationship()


class Deferral(Base):
    __tablename__ = "deferral"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("plan.id", ondelete="CASCADE"), nullable=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("order_.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(12))  # unavoidable | choice | manual
    code: Mapped[str] = mapped_column(String(30))
    text: Mapped[str] = mapped_column(Text)
    cost: Mapped[dict] = mapped_column(JSON, default=dict)
    streak: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    decided_by: Mapped[str] = mapped_column(String(60), default="Relay planner")
    next_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class Issue(Base):
    """Something a person must look at: a dock shortfall, a driver report, a receipt problem,
    a vehicle that went dark, a move that conflicted with what happened on the road."""

    __tablename__ = "issue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    severity: Mapped[str] = mapped_column(String(10), default="warn")
    status: Mapped[str] = mapped_column(String(10), default="open")  # open | decided | resolved
    trip_id: Mapped[int | None] = mapped_column(ForeignKey("trip.id", ondelete="SET NULL"), nullable=True)
    stop_id: Mapped[int | None] = mapped_column(ForeignKey("stop.id", ondelete="SET NULL"), nullable=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("order_.id", ondelete="SET NULL"), nullable=True)
    line_id: Mapped[int | None] = mapped_column(ForeignKey("order_line.id", ondelete="SET NULL"), nullable=True)
    qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(String(300), default="")
    raised_by: Mapped[str] = mapped_column(String(60), default="")
    raised_role: Mapped[str] = mapped_column(String(16), default="")
    raised_at: Mapped[datetime] = mapped_column(DateTime)
    decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    decision_text: Mapped[str | None] = mapped_column(String(300), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(60), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    acked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    photo_media_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class Proof(Base):
    __tablename__ = "proof"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    stop_id: Mapped[int] = mapped_column(ForeignKey("stop.id", ondelete="CASCADE"), index=True)
    receiver_name: Mapped[str] = mapped_column(String(60), default="")
    photo_media_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    signature_media_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    note: Mapped[str] = mapped_column(String(200), default="")
    captured_at: Mapped[datetime] = mapped_column(DateTime)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)


class Media(Base):
    __tablename__ = "media"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    content_type: Mapped[str] = mapped_column(String(40))
    size: Mapped[int] = mapped_column(Integer)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(TZ)


class Receipt(Base):
    __tablename__ = "receipt"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("order_.id", ondelete="CASCADE"), unique=True)
    by_name: Mapped[str] = mapped_column(String(60))
    at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(24))  # confirmed | confirmed_with_issues
    note: Mapped[str] = mapped_column(String(300), default="")
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)


class PendingMove(Base):
    """A dispatcher change to a vehicle that cannot hear it yet (no signal). It applies on the
    driver's next sync unless the driver already delivered: physical facts win."""

    __tablename__ = "pending_move"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("order_.id", ondelete="CASCADE"))
    from_trip_id: Mapped[int] = mapped_column(ForeignKey("trip.id", ondelete="CASCADE"))
    to_vehicle_id: Mapped[str] = mapped_column(String(10))
    to_trip_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10), default="queued")  # queued | applied | cancelled
    created_by: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolution: Mapped[str] = mapped_column(String(300), default="")


class Notice(Base):
    """A message for a face of the system: the dispatcher's feed, a dock, a driver, a store."""

    __tablename__ = "notice"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    audience: Mapped[str] = mapped_column(String(30), index=True)  # dispatch | depot:Kandy | vehicle:VEH057 | outlet:OUT105
    kind: Mapped[str] = mapped_column(String(8), default="blue")  # blue | ember | green | amber
    icon: Mapped[str] = mapped_column(String(16), default="info")
    title: Mapped[str] = mapped_column(String(140))
    body: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = mapped_column(DateTime)
    actions: Mapped[list] = mapped_column(JSON, default=list)
    key: Mapped[str | None] = mapped_column(String(60), nullable=True)
    entity: Mapped[str | None] = mapped_column(String(40), nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    read: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (Index("ix_notice_ws_aud", "workspace_id", "audience", "resolved"),)


class DeviceSeen(Base):
    __tablename__ = "device_seen"

    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True)
    last_seen_real: Mapped[datetime] = mapped_column(TZ)
    last_virtual: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    pending: Mapped[int] = mapped_column(Integer, default=0)


class Event(Base):
    __tablename__ = "event"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(40))
    actor_name: Mapped[str] = mapped_column(String(60), default="")
    actor_role: Mapped[str] = mapped_column(String(16), default="")
    entity: Mapped[str] = mapped_column(String(40), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    virtual_at: Mapped[datetime] = mapped_column(DateTime)
    device_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    server_at: Mapped[datetime] = mapped_column(TZ)
    client_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)

    __table_args__ = (
        UniqueConstraint("workspace_id", "client_event_id", name="uq_event_client_id"),
        Index("ix_event_ws_id", "workspace_id", "id"),
    )
