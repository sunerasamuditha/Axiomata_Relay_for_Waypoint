"""Seeding: the shared datasets, the four judge accounts, and the demo workspace.

python -m relay_api.seed                # reference + users + 'main' workspace if missing
python -m relay_api.seed --reset main   # wipe and reseed a workspace
"""

from __future__ import annotations

import argparse
import csv
import random
import re
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    CalendarDay,
    Deferral,
    Depot,
    District,
    Event,
    Issue,
    Media,
    Notice,
    Order,
    OrderLine,
    Outlet,
    PendingMove,
    Plan,
    Product,
    Proof,
    Receipt,
    ServiceAllowance,
    StandingOrder,
    Stop,
    TrafficSpeed,
    Trip,
    User,
    Vehicle,
    VehicleDay,
    Workspace,
)
from ..security import hash_password
from .catalog import products
from .demo_day import (
    CARRY_OVER,
    PREVIOUS_DATE,
    SERVICE_DATE,
    STANDING,
    USUAL_OVERRIDES,
    WORKSHOP,
    build_orders,
    load_profiles,
)
from .names import OUTLET_NAMES, driver_for, manager_for

SEED_VERSION = 3
SEED_CLOCK = datetime(2026, 9, 29, 15, 20)

USERS = [
    # email, name, short, initials, color, role, title, depot, dock, vehicle, outlet
    (
        "nirosha@waypoint.lk",
        "Nirosha Perera",
        "Nirosha",
        "NP",
        "#1F4BFF",
        "dispatcher",
        "Dispatcher · Peliyagoda planning office",
        None,
        None,
        None,
        None,
    ),
    ("kasun@waypoint.lk", "Kasun Bandara", "Kasun", "KB", "#6B4DFF", "loader", "Loader · Kandy Hub, Dock 1", "Kandy", 1, None, None),
    (
        "sunil@waypoint.lk",
        "Sunil Rathnayake",
        "Sunil",
        "SR",
        "#F0512A",
        "driver",
        "Driver · VEH057 reefer van",
        "Kandy",
        None,
        "VEH057",
        None,
    ),
    (
        "fathima@waypoint.lk",
        "Fathima Rizwan",
        "Fathima",
        "FR",
        "#0F9D6B",
        "store",
        "Store manager · Nuwara Eliya Town",
        None,
        None,
        None,
        "OUT105",
    ),
    # extra accounts (not needed for the walkthrough)
    (
        "ravi@waypoint.lk",
        "Ravindran Selvam",
        "Ravindran",
        "RS",
        "#C2410C",
        "loader",
        "Loader · Peliyagoda DC, Dock 1",
        "Peliyagoda",
        1,
        None,
        None,
    ),
    (
        "roshan@waypoint.lk",
        "Roshan Bandara",
        "Roshan",
        "RB",
        "#0E7490",
        "driver",
        "Driver · VEH042 reefer truck",
        "Kandy",
        None,
        "VEH042",
        None,
    ),
]

# Each store manager's fixed delivery PIN (4 to 6 digits): typed on the driver's phone at the door to
# confirm the handover (domain/handover.py). Fathima sees hers in Profile.
STORE_PINS = {"fathima@waypoint.lk": "4826"}


def data_dir() -> Path:
    return Path(get_settings().data_dir)


def _rows(name: str) -> list[dict]:
    with open(data_dir() / "reference" / name, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def seed_reference(db: Session) -> bool:
    if db.scalar(select(Outlet.id).limit(1)):
        return False
    db.add_all(
        [
            Depot(id="Peliyagoda", code="PEL", name="Peliyagoda DC", kind="Distribution centre", docks=3, bays_per_dock=5),
            Depot(id="Kandy", code="KDY", name="Kandy Hub", kind="Regional hub", docks=1, bays_per_dock=5),
        ]
    )
    for r in _rows("district_travel.csv"):
        db.add(
            District(
                name=r["district"],
                depot=r["depot"],
                road_class=r["road_class"],
                free_flow_kmh=float(r["free_flow_kmh"]),
                d2d_km=float(r["depot_to_district_km"]),
                d2d_min=int(float(r["depot_to_district_freeflow_min"])),
                inter_km=float(r["inter_stop_km"]),
                inter_min=int(float(r["inter_stop_freeflow_min"])),
            )
        )
    db.flush()
    profiles_path = data_dir() / "demo" / "outlet_profiles.json"
    profiles = load_profiles(profiles_path) if profiles_path.exists() else {}
    for r in _rows("outlets.csv"):
        n = int(r["outlet_id"][3:])
        prof = profiles.get(r["outlet_id"], {})
        ua = USUAL_OVERRIDES.get((r["outlet_id"], "ambient")) or (prof.get("ambient") or {}).get("usual_vehicle")
        uc = USUAL_OVERRIDES.get((r["outlet_id"], "chilled")) or (prof.get("chilled") or {}).get("usual_vehicle")
        db.add(
            Outlet(
                id=r["outlet_id"],
                name=OUTLET_NAMES.get(n, r["outlet_id"]),
                brand=r["brand"],
                district=r["district"],
                depot=r["depot"],
                dock_type=r["dock_type"],
                parking=r["parking_constraint"],
                mall_window=r["mall_window"] or None,
                window_open=r["window_open_time"],
                window_close=r["window_close_time"],
                usual_vehicle_ambient=ua,
                usual_vehicle_chilled=uc,
                manager=manager_for(n),
            )
        )
    for r in _rows("vehicles.csv"):
        db.add(
            Vehicle(
                id=r["vehicle_id"],
                type=r["type"],
                temp=r["temp"],
                weight_cap_kg=float(r["weight_cap_kg"]),
                volume_cap_m3=float(r["volume_cap_m3"]),
                fuel_type=r["fuel_type"],
                km_per_l=float(r["km_per_l"]),
                weekly_fuel_quota_l=float(r["weekly_fuel_quota_l"]),
                depot=r["depot"],
                driver_name=driver_for(r["vehicle_id"]),
            )
        )
    for r in _rows("service_allowance.csv"):
        db.add(ServiceAllowance(brand=r["brand"], dock_type=r["dock_type"], minutes=int(float(r["service_allowance_min"]))))
    for r in _rows("calendar.csv"):
        db.add(
            CalendarDay(
                date=date.fromisoformat(r["date"]),
                dow=int(r["dow"]),
                is_payday=r["is_payday"] == "1",
                festival=r["festival"] or None,
                festival_ramp=float(r["festival_ramp"] or 0),
                is_holiday=r["is_holiday"] == "1",
                monsoon=r["monsoon"] == "1",
                is_operating=r["is_operating"] == "1",
                iso_year=int(r["iso_year"]),
                iso_week=int(r["iso_week"]),
            )
        )
    for r in _rows("traffic_speed.csv"):
        db.add(TrafficSpeed(district=r["district"], hour=int(r["hour"]), monsoon=int(r["monsoon"]), speed_index=float(r["speed_index"])))
    for p in products():
        db.add(
            Product(
                sku=p["sku"],
                name=p["name"],
                brand=p["brand"],
                temp=p["temp"],
                unit_kg=p["unit_kg"],
                unit_m3=p["unit_m3"],
                uom=p["uom"],
                share=p["share"],
                sort=p["sort"],
            )
        )
    db.flush()
    return True


def seed_users(db: Session) -> int:
    pw = hash_password(get_settings().demo_password)
    n = 0
    for email, name, short, ini, col, role, title, depot, dock, vehicle, outlet in USERS:
        pin = STORE_PINS.get(email) if role == "store" else None
        if pin and not re.fullmatch(r"[0-9]{4,6}", pin):
            raise ValueError(f"The delivery PIN for {email} must be 4 to 6 digits.")
        u = db.scalar(select(User).where(User.email == email))
        if u:
            if pin and not u.delivery_pin:
                u.delivery_pin = pin  # an account seeded before PINs existed gets its PIN on the next start
            continue
        db.add(
            User(
                email=email,
                name=name,
                short_name=short,
                initials=ini,
                color=col,
                role=role,
                title=title,
                password_hash=pw,
                depot=depot,
                dock=dock,
                vehicle_id=vehicle,
                outlet_id=outlet,
                delivery_pin=pin,
            )
        )
        n += 1
    db.flush()
    return n


def wipe_workspace(db: Session, ws_id: str) -> None:
    for model in (PendingMove, Proof, Receipt, Issue, Deferral, Stop, Trip, Plan, Notice, Event, Media, StandingOrder, VehicleDay):
        db.execute(delete(model).where(model.workspace_id == ws_id))
    order_ids = select(Order.id).where(Order.workspace_id == ws_id)
    db.execute(delete(OrderLine).where(OrderLine.order_id.in_(order_ids)))
    db.execute(text("update order_ set origin_order_id = null where workspace_id = :w"), {"w": ws_id})
    db.execute(delete(Order).where(Order.workspace_id == ws_id))
    db.execute(text("delete from device_seen where workspace_id = :w"), {"w": ws_id})


def create_workspace(db: Session, ws_id: str, name: str, code: str, *, sandbox: bool = False) -> Workspace:
    from ..domain.clock import utcnow
    from ..domain.eta import RefCache
    from ..domain.events import post_notice

    RefCache.load(db)
    ws = db.get(Workspace, ws_id)
    if ws:
        wipe_workspace(db, ws_id)
    else:
        ws = Workspace(
            id=ws_id,
            name=name,
            code=code,
            created_at=utcnow(),
            clock_anchor_real=utcnow(),
            clock_anchor_virtual=SEED_CLOCK,
            service_date=SERVICE_DATE,
            sandbox=sandbox,
        )
        db.add(ws)
    ws.clock_anchor_real, ws.clock_anchor_virtual, ws.clock_rate, ws.clock_paused = utcnow(), SEED_CLOCK, 1.0, True
    ws.service_date, ws.orders_closed_at, ws.seed_version = SERVICE_DATE, None, SEED_VERSION
    db.flush()

    # vehicles: workshop and fuel already used this week (Monday + Tuesday)
    rng = random.Random(5)
    backs = {
        "VEH001": date(2026, 10, 5),
        "VEH002": date(2026, 10, 5),
        "VEH004": date(2026, 10, 30),
        "VEH005": date(2026, 10, 30),
        "VEH035": date(2026, 10, 12),
        "VEH043": date(2026, 10, 7),
    }
    for v in RefCache.vehicles.values():
        used = round(v.weekly_fuel_quota_l * rng.uniform(0.25, 0.45), 1)
        if v.id == "VEH044":
            used = v.weekly_fuel_quota_l - 38  # nearly out of this week's quota: the planner must notice
        db.add(
            VehicleDay(
                workspace_id=ws.id,
                date=SERVICE_DATE,
                vehicle_id=v.id,
                status="in_workshop" if v.id in WORKSHOP else "available",
                fuel_used_l=used,
                back_on=backs.get(v.id),
                note="Scheduled maintenance" if v.id in WORKSHOP else "",
            )
        )

    prods = [p for p in products()]
    catalog: dict[str, list[dict]] = {}
    for p in prods:
        catalog.setdefault(f"{p['brand']}:{p['temp']}", []).append(p)
    profiles = load_profiles(data_dir() / "demo" / "outlet_profiles.json")
    outlets = {o.id: {"brand": o.brand, "district": o.district, "depot": o.depot} for o in RefCache.outlets.values()}
    demo = build_orders(profiles, outlets, catalog)
    by_sku = {p["sku"]: p for p in prods}
    seq = 30401

    def add_order(d, service_date, status, placed_at, channel, **kw) -> Order:
        nonlocal seq
        o = Order(
            workspace_id=ws.id,
            ref=f"WP-{seq}",
            outlet_id=d.outlet_id,
            service_date=service_date,
            brand=RefCache.outlets[d.outlet_id].brand,
            temp=d.temp,
            units=d.units,
            weight_kg=d.weight_kg,
            volume_m3=d.volume_m3,
            status=status,
            channel=channel,
            placed_at=placed_at,
            deferred_yesterday=d.deferred_yesterday,
            days_since_served=d.days_since_served,
            note=d.note,
            **kw,
        )
        o.lines = [OrderLine(sku=s, name=by_sku[s]["name"], qty=q, uom=by_sku[s]["uom"]) for s, q in d.lines]
        seq += 1
        db.add(o)
        return o

    # Tuesday's history for the carried-over orders (deferred on Tuesday's run)
    origins = {}
    for d in demo:
        if d.carry_over:
            tue = add_order(d, PREVIOUS_DATE, "deferred", datetime(2026, 9, 28, 14, 30), "app")
            tue.deferred_yesterday = d.days_since_served > 2
            tue.days_since_served = d.days_since_served - 1
            origins[(d.outlet_id, d.temp)] = tue
    db.flush()
    for tue in origins.values():
        db.add(
            Deferral(
                workspace_id=ws.id,
                plan_id=None,
                order_id=tue.id,
                kind="choice",
                code="Refrigerated space",
                text="Refrigerated space ran out on Tuesday's run.",
                streak=2 if tue.deferred_yesterday else 1,
                created_at=datetime(2026, 9, 28, 16, 31),
                next_date=SERVICE_DATE,
            )
        )
    # Fathima's Tuesday dry goods, delivered and received this morning
    fathima_tue = add_order(
        type(
            "D",
            (),
            {
                "outlet_id": "OUT105",
                "temp": "ambient",
                "units": 34,
                "weight_kg": 247.2,
                "volume_m3": 1.27,
                "deferred_yesterday": False,
                "days_since_served": 1,
                "note": "",
                "lines": [("FR-DRY", 19), ("FR-BEV", 10), ("FR-HSE", 5)],
            },
        )(),
        PREVIOUS_DATE,
        "received",
        datetime(2026, 9, 28, 13, 5),
        "app",
    )
    # Wednesday's orders
    for d in sorted(demo, key=lambda x: x.placed_minute):
        if d.carry_over:
            continue
        add_order(
            d, SERVICE_DATE, "placed", datetime.combine(PREVIOUS_DATE, datetime.min.time()) + timedelta(minutes=d.placed_minute), d.channel
        )
    db.flush()
    for d in demo:
        if d.carry_over:
            origin = origins[(d.outlet_id, d.temp)]
            o = add_order(
                d,
                SERVICE_DATE,
                "placed",
                datetime(2026, 9, 29, 5, 10),
                "carryover",
                origin_order_id=origin.id,
                deferral_streak=1 if not origin.deferred_yesterday else 2,
            )
            o.note = "Deferred on Tue 29 Sep · carried over"
    for outlet_id, temps in STANDING.items():
        for temp, lines in temps.items():
            db.add(StandingOrder(workspace_id=ws.id, outlet_id=outlet_id, temp=temp, lines=[[s, q] for s, q in lines]))
    db.flush()
    for ln in fathima_tue.lines:
        ln.load_state, ln.loaded_qty, ln.delivered_qty, ln.received_qty = "loaded", ln.qty, ln.qty, ln.qty
    db.add(
        Receipt(workspace_id=ws.id, order_id=fathima_tue.id, by_name="Fathima Rizwan", at=datetime(2026, 9, 29, 6, 31), status="confirmed")
    )

    # what each face sees at 15:20 on Tuesday
    n_orders = sum(1 for d in demo if not d.carry_over)
    at = SEED_CLOCK
    post_notice(
        db,
        ws,
        "outlet:OUT105",
        "Dry goods received",
        "You confirmed Tuesday's delivery at 06:31. Thank you.",
        kind="green",
        icon="check",
        at=datetime(2026, 9, 29, 6, 31),
    )
    post_notice(
        db,
        ws,
        "outlet:OUT105",
        "Chilled order moved to Wednesday",
        "Refrigerated space ran out on Tuesday's hill run. It is the second run in a row, so it is first in line on Wednesday.",
        kind="ember",
        icon="cal",
        at=datetime(2026, 9, 29, 5, 10),
    )
    post_notice(
        db,
        ws,
        "outlet:OUT105",
        "Wednesday's order closes at 4:00 PM",
        "Your usual dry-goods order (34 cases) is placed automatically at the cutoff if you don't change it.",
        kind="blue",
        icon="clock",
        key="cutoff",
        at=datetime(2026, 9, 29, 15, 0),
    )
    post_notice(
        db,
        ws,
        "dispatch",
        "Cold fleet is short tomorrow",
        "5 of 9 refrigerated vehicles at Peliyagoda and 1 of 7 at Kandy are in the workshop. VEH001 and VEH002 return on Monday.",
        kind="ember",
        icon="wrench",
        at=datetime(2026, 9, 29, 9, 0),
    )
    post_notice(
        db,
        ws,
        "dispatch",
        "4 repeat skips from Tuesday",
        "Nuwara Eliya Town, Puttalam Town (5 days without chilled), Kurunegala Town and Nugegoda were deferred yesterday and are carried over.",
        kind="ember",
        icon="alert",
        actions=[["Review", "dview:orders"]],
        at=datetime(2026, 9, 29, 9, 5),
    )
    post_notice(
        db,
        ws,
        "dispatch",
        "Orders for Wed 30 Sep close at 4:00 PM",
        f"{n_orders} orders so far from {len({d.outlet_id for d in demo})} outlets. Close them to plan the day.",
        kind="blue",
        icon="list",
        actions=[["Open queue", "dview:orders"]],
        key="cutoff",
        at=at,
    )
    post_notice(
        db,
        ws,
        "depot:Kandy",
        "Wednesday's plan arrives after the 4:00 PM cutoff",
        "Nirosha publishes it after closing orders. Your trucks for Dock 1 appear here.",
        kind="blue",
        icon="clock",
        key="plan",
        at=at,
    )
    db.flush()
    return ws


def ensure_seeded(db: Session) -> dict:
    out = {"reference": seed_reference(db), "users": seed_users(db), "workspace": False}
    if not db.get(Workspace, "main"):
        create_workspace(db, "main", "Shared demo", "MAIN")
        out["workspace"] = True
    db.commit()
    return out


SEED_LOCK = 424242


def ensure_seeded_locked(db: Session) -> dict | str:
    """ensure_seeded under an advisory lock, so two instances starting together (Cloud Run
    scale-out, or the entrypoint and the app's start-up hook) never seed at the same time."""
    from ..db import held_lock

    with held_lock(SEED_LOCK) as got:
        if not got:
            return "another process is seeding; skipped"
        try:
            return ensure_seeded(db)
        except Exception:
            db.rollback()
            raise


def new_sandbox_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "RLY-" + "".join(secrets.choice(alphabet) for _ in range(4))


def main(argv: list[str] | None = None) -> int:
    from ..db import SessionLocal

    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", metavar="WORKSPACE", help="wipe and reseed a workspace (e.g. main)")
    ap.add_argument(
        "--if-empty",
        action="store_true",
        help="container start: add whatever is missing (reference data, accounts, the 'main' workspace) and never wipe",
    )
    a = ap.parse_args(argv)
    from ..db import held_lock

    with SessionLocal() as db:
        if not a.reset:
            print("seed:", ensure_seeded_locked(db))
            return 0
        with held_lock(SEED_LOCK) as got:
            if not got:
                print("another process is seeding; try again in a moment")
                return 1
            seed_reference(db)
            seed_users(db)
            ws = db.get(Workspace, a.reset)
            create_workspace(db, a.reset, ws.name if ws else "Shared demo", ws.code if ws else "MAIN", sandbox=bool(ws and ws.sandbox))
            from ..domain.events import notify

            # open browsers reload their data, exactly as after "Reset demo" in the app
            notify(db, a.reset, ["dispatch", "plan", "clock", "depot:Kandy", "depot:Peliyagoda", "vehicle:*", "outlet:*"], kind="reset")
            db.commit()
            print(f"workspace {a.reset} reseeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

_ = CARRY_OVER
