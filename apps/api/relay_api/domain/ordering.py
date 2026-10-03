"""Store ordering: the catalogue, the cutoff, and turning a basket into orders.

A basket can hold chilled and ambient lines; they become separate orders because they travel in
different vehicles (the booklet: a Fresh outlet can have two orders for the same delivery day).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..models import Order, OrderLine, Product, StandingOrder, Workspace
from .clock import cutoff_for, now_virtual
from .eta import RefCache
from .events import post_notice, record


class OrderingError(Exception):
    pass


def open_service_date(ws: Workspace):
    """The delivery day stores are ordering for right now."""
    now = now_virtual(ws)
    if ws.orders_closed_at is None and now < cutoff_for(ws.service_date):
        return ws.service_date
    return ws.service_date + timedelta(days=1)


def place_order(
    db: Session,
    ws: Workspace,
    outlet_id: str,
    temp: str,
    lines: list[tuple[str, int]],
    *,
    channel: str = "app",
    by: int | None = None,
    at: datetime | None = None,
    service_date=None,
    note: str = "",
) -> Order:
    from .planning import next_ref

    products = {p.sku: p for p in db.query(Product)}
    ou = RefCache.load(db).outlets[outlet_id]
    lines = [(s, int(q)) for s, q in lines if int(q) > 0]
    if not lines:
        raise OrderingError("Add at least one line.")
    for s, _ in lines:
        p = products.get(s)
        if not p or p.brand != ou.brand or p.temp != temp:
            raise OrderingError(f"{s} cannot be ordered here.")
    units = sum(q for _, q in lines)
    o = Order(
        workspace_id=ws.id,
        ref=next_ref(db, ws),
        outlet_id=outlet_id,
        service_date=service_date or ws.service_date,
        brand=ou.brand,
        temp=temp,
        units=units,
        weight_kg=round(sum(products[s].unit_kg * q for s, q in lines), 1),
        volume_m3=round(sum(products[s].unit_m3 * q for s, q in lines), 3),
        status="placed",
        channel=channel,
        placed_at=at or now_virtual(ws),
        placed_by=by,
        note=note,
    )
    o.lines = [OrderLine(sku=s, name=products[s].name, qty=q, uom=products[s].uom) for s, q in lines]
    db.add(o)
    db.flush()
    return o


def catalog_for(db: Session, ws: Workspace, outlet_id: str) -> dict:
    ou = RefCache.load(db).outlets[outlet_id]
    products = db.query(Product).filter_by(brand=ou.brand).order_by(Product.temp.desc(), Product.sort).all()
    standing = {(so.temp): dict(so.lines) for so in db.query(StandingOrder).filter_by(workspace_id=ws.id, outlet_id=outlet_id)}
    day = open_service_date(ws)
    existing = (
        db.query(Order)
        .filter(
            Order.workspace_id == ws.id,
            Order.outlet_id == outlet_id,
            Order.service_date == day,
            Order.channel.in_(("app", "phone", "standing")),
        )
        .all()
    )
    prefill: dict[str, int] = {}
    for _temp, lines in standing.items():
        prefill.update({k: int(v) for k, v in lines.items()})
    for o in existing:
        for line in o.lines:
            prefill[line.sku] = line.qty
    return {
        "service_date": day.isoformat(),
        "cutoff": cutoff_for(day).isoformat(),
        "window": [ou.window_open, ou.window_close],
        "products": [
            {
                "sku": p.sku,
                "name": p.name,
                "temp": p.temp,
                "uom": p.uom,
                "unit_kg": p.unit_kg,
                "unit_m3": p.unit_m3,
                "qty": prefill.get(p.sku, 0),
            }
            for p in products
        ],
        "standing": {t: v for t, v in standing.items()},
        "existing": [{"id": o.id, "ref": o.ref, "temp": o.temp, "status": o.status, "units": o.units} for o in existing],
    }


def submit_basket(db: Session, ws: Workspace, user, lines: dict[str, int]) -> list[Order]:
    """Place (or replace) the store's orders for the open delivery day."""
    ou = RefCache.load(db).outlets[user.outlet_id]
    day = open_service_date(ws)
    now = now_virtual(ws)
    if day == ws.service_date and ws.orders_closed_at:
        raise OrderingError("Orders for this day are closed.")
    products = {p.sku: p for p in db.query(Product).filter_by(brand=ou.brand)}
    by_temp: dict[str, list[tuple[str, int]]] = {}
    for sku, qty in lines.items():
        if sku not in products:
            raise OrderingError(f"Unknown product {sku}")
        if int(qty) > 0:
            by_temp.setdefault(products[sku].temp, []).append((sku, int(qty)))
    if not by_temp:
        raise OrderingError("The order is empty.")
    placed = []
    for temp, ls in by_temp.items():
        old = (
            db.query(Order)
            .filter(
                Order.workspace_id == ws.id,
                Order.outlet_id == user.outlet_id,
                Order.service_date == day,
                Order.temp == temp,
                Order.channel.in_(("app", "phone", "standing")),
                Order.status == "placed",
            )
            .all()
        )
        for o in old:
            o.status = "cancelled"
        o = place_order(db, ws, user.outlet_id, temp, ls, channel="app", by=user.id, at=now, service_date=day)
        placed.append(o)
        if temp in ("ambient", "chilled"):
            so = db.get(StandingOrder, (ws.id, user.outlet_id, temp))
            if so:
                so.lines = [[s, q] for s, q in ls]
    refs = ", ".join(o.ref for o in placed)
    record(
        db,
        ws,
        "order.placed",
        actor_name=user.short_name,
        actor_role="store",
        entity=f"outlet:{user.outlet_id}",
        payload={"refs": [o.ref for o in placed], "service_date": day.isoformat()},
        topics=[f"outlet:{user.outlet_id}", "orders"],
    )
    post_notice(
        db,
        ws,
        "dispatch",
        f"New order · {ou.name}",
        f"{refs} for {day:%a %-d %b} from the store app.",
        kind="blue",
        icon="list",
        actions=[["Open queue", "dview:orders"]],
    )
    post_notice(
        db,
        ws,
        f"outlet:{user.outlet_id}",
        f"Order received for {day:%A}",
        f"{refs}. Dispatch plans after the 4:00 PM cutoff and you will see the arrival time here.",
        kind="green",
        icon="check",
    )
    return placed
