"""Store-manager PIN handover (needs Postgres; see conftest.py).

Only Fathima's own profile carries her PIN; the driver's run carries a one-way check value instead;
a delivery without a valid PIN proof is still recorded (physical facts win) but flagged to dispatch.
"""

from __future__ import annotations

import re
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_ready")

FATHIMA = "fathima@waypoint.lk"


def login(client, email, code):
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"email": email, "password": "relay2026", "workspace": code})
    assert r.status_code == 200, r.text


def ev(type_, payload):
    return {"client_event_id": uuid.uuid4().hex, "type": type_, "payload": payload}


def publish(client, code):
    login(client, "nirosha@waypoint.lk", code)
    assert client.post("/api/dispatch/plans", json={"policy": "balanced", "publish": True}).status_code == 200


def released_run(client, code) -> dict:
    """A published plan with VEH057 loaded and released at the Kandy dock; returns Sunil's run."""
    publish(client, code)
    login(client, "kasun@waypoint.lk", code)
    trip = next(t for t in client.get("/api/dock/queue").json()["trips"] if t["vehicle"] == "VEH057")
    detail = client.get(f"/api/dock/trips/{trip['id']}").json()
    for ln in (ln for v in detail["load_order"] for ln in v["lines"]):
        assert client.post(f"/api/dock/lines/{ln['id']}/check", json={"loaded": True}).status_code == 200
    r = client.post(f"/api/dock/trips/{trip['id']}/release", json={"temp_c": 3, "seal": "KH-58213"})
    assert r.status_code == 200, r.text
    login(client, "sunil@waypoint.lk", code)
    return client.get("/api/driver/run").json()


def visit_at(run: dict, outlet: str) -> dict:
    return next(v for t in run["trips"] for v in t["visits"] if v["outlet"] == outlet)


def walk(x, key=None):
    """(key, value) for every value in a JSON document, at any depth; inside a list, the list's key."""
    yield key, x
    if isinstance(x, dict):
        for k, v in x.items():
            yield from walk(v, k)
    elif isinstance(x, list):
        for v in x:
            yield from walk(v, key)


def test_only_the_store_manager_sees_her_delivery_pin(client, sandbox_code):
    from relay_api.seed import STORE_PINS

    login(client, FATHIMA, sandbox_code)
    r = client.get("/api/store/profile")
    assert r.status_code == 200, r.text
    profile = r.json()
    assert profile["delivery_pin"] == STORE_PINS[FATHIMA] == "4826"
    assert profile["name"] == "Fathima Rizwan" and profile["outlet"]["id"] == "OUT105"
    assert "no-store" in r.headers["cache-control"]
    assert "delivery_pin" not in client.get("/api/auth/me").json()
    for email in ("nirosha@waypoint.lk", "kasun@waypoint.lk", "sunil@waypoint.lk"):
        login(client, email, sandbox_code)
        assert client.get("/api/store/profile").status_code == 403, email


def test_the_run_carries_a_check_value_never_the_pin(client, sandbox_code):
    from relay_api.domain.handover import pin_check, pin_proof
    from relay_api.seed import STORE_PINS

    pin = STORE_PINS[FATHIMA]
    publish(client, sandbox_code)
    login(client, "sunil@waypoint.lk", sandbox_code)
    run = client.get("/api/driver/run").json()
    visits = [v for t in run["trips"] for v in t["visits"]]
    mine = [v for v in visits if v["outlet"] == "OUT105"]
    assert mine, "Fathima's store should be on Sunil's run"
    assert all(v["pin_required"] and v["pin_check"] == pin_check(v["visit_id"], pin) for v in mine)
    assert all(not v["pin_required"] and v["pin_check"] is None for v in visits if v["outlet"] != "OUT105")
    # neither the PIN nor what the phone sends back is in the run: no other field about a PIN, no value
    # equal to it, no notice quoting it. Not a raw substring search: ids ("order:4826") are serials shared
    # by every workspace, and timestamps carry microseconds, so the same digits turn up by chance
    pairs = list(walk(run))
    assert {k for k, _ in pairs if k and "pin" in k} <= {"pin_required", "pin_check", "pin_verified"}
    assert not [k for k, v in pairs if v == pin]
    sentences = [n[f] for n in run["notices"] for f in ("title", "body")]
    assert not [s for s in sentences if re.search(rf"(?<![0-9]){pin}(?![0-9])", s)]
    proofs = {pin_proof(v["visit_id"], pin) for v in mine}
    assert not [k for k, v in pairs if isinstance(v, str) and v in proofs]


def test_a_wrong_pin_still_records_the_delivery_and_tells_dispatch(client, sandbox_code):
    from relay_api.domain.handover import pin_proof
    from relay_api.seed import STORE_PINS

    wrong = "1111" if STORE_PINS[FATHIMA] != "1111" else "2222"
    v = visit_at(released_run(client, sandbox_code), "OUT105")
    done = {"stop_ids": v["stop_ids"], "visit_id": v["visit_id"], "outcome": "delivered", "receiver": "Night guard"}
    r = client.post(
        "/api/sync", json={"device_id": "test", "events": [ev("stop.complete", {**done, "pin_proof": pin_proof(v["visit_id"], wrong)})]}
    )
    assert [x["status"] for x in r.json()["results"]] == ["applied"], r.text
    after = visit_at(client.get("/api/driver/run").json(), "OUT105")
    assert after["status"] in ("delivered", "partial")
    assert after["proof"]["pin_verified"] is False and after["proof"]["receiver"] == "Night guard"
    login(client, "nirosha@waypoint.lk", sandbox_code)
    feed = client.get("/api/dispatch/snapshot").json()["feed"]
    assert any("without the store's PIN" in n["title"] for n in feed)
    login(client, FATHIMA, sandbox_code)
    assert any("without your PIN" in n["body"] for n in client.get("/api/store/home").json()["notices"])


def test_resending_a_pin_confirmed_delivery_answers_duplicate(client, sandbox_code):
    from relay_api.domain.handover import pin_proof
    from relay_api.seed import STORE_PINS

    v = visit_at(released_run(client, sandbox_code), "OUT105")
    done = ev(
        "stop.complete",
        {
            "stop_ids": v["stop_ids"],
            "visit_id": v["visit_id"],
            "outcome": "delivered",
            "pin_proof": pin_proof(v["visit_id"], STORE_PINS[FATHIMA]),
        },
    )
    first = client.post("/api/sync", json={"device_id": "test", "events": [done]}).json()
    assert first["results"][0]["status"] == "applied", first
    again = client.post("/api/sync", json={"device_id": "test", "events": [done]}).json()
    assert again["results"][0]["status"] == "duplicate", again
    proof = visit_at(client.get("/api/driver/run").json(), "OUT105")["proof"]
    assert proof["pin_verified"] is True and proof["receiver"] == "Fathima Rizwan"
