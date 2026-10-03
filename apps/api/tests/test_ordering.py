"""Ordering rules: the 4 PM cutoff, standing orders placed at the cutoff, closing the day once."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("db_ready")


def login(client, email, code):
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"email": email, "password": "relay2026", "workspace": code})
    assert r.status_code == 200, r.text


def test_before_the_cutoff_stores_order_for_tomorrow(client, sandbox_code):
    login(client, "fathima@waypoint.lk", sandbox_code)
    cat = client.get("/api/store/catalog").json()
    # the demo clock starts on Tuesday 29 Sep at 15:20: orders are still open for Wednesday
    assert cat["service_date"] == "2026-09-30"
    assert cat["cutoff"].startswith("2026-09-29T16:00")
    home = client.get("/api/store/home").json()
    assert home["ordering"]["open"] is True


def test_after_the_cutoff_new_orders_go_to_the_next_day(client, sandbox_code):
    login(client, "nirosha@waypoint.lk", sandbox_code)
    assert client.post("/api/demo/clock", json={"op": "advance", "minutes": 45}).status_code == 200  # 16:05

    login(client, "fathima@waypoint.lk", sandbox_code)
    cat = client.get("/api/store/catalog").json()
    assert cat["service_date"] == "2026-10-01"
    basket = {p["sku"]: 2 for p in cat["products"] if p["temp"] == "chilled"}
    r = client.post("/api/store/orders", json={"lines": basket})
    assert r.status_code == 200, r.text
    assert {o["service_date"] for o in r.json()["orders"]} == {"2026-10-01"}


def test_closing_orders_happens_once_and_fills_in_standing_orders(client, sandbox_code):
    login(client, "nirosha@waypoint.lk", sandbox_code)
    first = client.post("/api/dispatch/orders/close")
    assert first.status_code == 200, first.text
    out = first.json()
    assert out["total"] >= out["confirmed"] > 0
    again = client.post("/api/dispatch/orders/close")
    assert again.status_code == 409 and "already closed" in again.json()["detail"]
    # stores can no longer change Wednesday
    login(client, "fathima@waypoint.lk", sandbox_code)
    assert client.get("/api/store/catalog").json()["service_date"] == "2026-10-01"


def test_an_empty_basket_is_refused(client, sandbox_code):
    login(client, "fathima@waypoint.lk", sandbox_code)
    r = client.post("/api/store/orders", json={"lines": {}})
    assert r.status_code == 409 and "empty" in r.json()["detail"].lower()
