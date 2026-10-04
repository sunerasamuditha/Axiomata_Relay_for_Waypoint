"""The judge walkthrough, end to end, through the HTTP API (needs Postgres; see conftest.py).

Store orders -> dispatcher closes and plans -> lever -> publish -> dock loads, flags a shortfall,
dispatcher decides, dock releases -> driver runs online and offline (idempotent sync), Fathima
confirms her handover with her PIN, the van goes dark, a move is queued and then cancelled because
the delivery happened first -> store confirms receipt with an issue that reaches dispatch.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta

import pytest

PNG = (
    "data:image/png;base64,"
    + base64.b64encode(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
            "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
        )
    ).decode()
)


def login(client, email, code):
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"email": email, "password": "relay2026", "workspace": code})
    assert r.status_code == 200, r.text
    return r.json()


def ev(type_, payload, at=None, offline=False):
    return {"client_event_id": uuid.uuid4().hex, "type": type_, "payload": payload, "at": at, "offline": offline}


@pytest.mark.usefixtures("db_ready")
def test_full_walkthrough(client, sandbox_code, db_session):
    code = sandbox_code

    # 1. store manager places Wednesday's dry goods before the cutoff
    me = login(client, "fathima@waypoint.lk", code)
    assert me["role"] == "store" and me["home"] == "/store"
    cat = client.get("/api/store/catalog").json()
    assert cat["service_date"] == "2026-09-30"
    basket = {p["sku"]: (p["qty"] or 0) for p in cat["products"] if p["temp"] == "ambient"}
    basket["FR-BEV"] = 12
    r = client.post("/api/store/orders", json={"lines": basket})
    assert r.status_code == 200, r.text
    assert r.json()["orders"][0]["temp"] == "ambient"
    # roles are enforced server-side
    assert client.get("/api/dispatch/snapshot").status_code == 403

    # 2. dispatcher closes orders and plans
    login(client, "nirosha@waypoint.lk", code)
    snap = client.get("/api/dispatch/snapshot").json()
    assert snap["phase"] == "ordering" and snap["plan"] is None
    r = client.post("/api/dispatch/plans", json={"policy": "balanced"})
    assert r.status_code == 200, r.text
    plan = r.json()
    k = plan["kpis"]
    assert k["served"] >= 120 and k["deferred"] >= 3  # demand exceeds capacity on purpose
    snap = client.get("/api/dispatch/snapshot").json()
    assert snap["phase"] == "draft"
    deferred = [o for o in snap["orders"] if o["deferred"]]
    assert any(o["deferred"]["kind"] == "unavoidable" and o["deferred"]["code"] == "Too big" for o in deferred)
    assert all(o["deferred"]["text"] for o in deferred)
    sunil = next(t for t in snap["trips"] if t["vehicle"] == "VEH057")
    assert sunil["district"] == "Nuwara Eliya" and sunil["controlled"] == "human"

    # 3. the deferral lever previews the other policies
    lev = client.get(f"/api/dispatch/plans/{plan['plan_id']}/lever").json()
    assert set(lev["policies"]) == {"throughput", "balanced", "fairness"}
    assert lev["policies"]["fairness"]["kpis"]["repeat_skips"] == 0

    # an illegal manual move is refused with a human reason
    chilled = next(o for o in snap["orders"] if o["temp"] == "chilled" and o["trip"] and o["depot"] == "Peliyagoda")
    dry = next(v for v in snap["vehicles"] if v["depot"] == "Peliyagoda" and v["temp"] == "ambient" and v["status"] == "ok")
    r = client.post("/api/dispatch/moves", json={"order_id": chilled["id"], "vehicle_id": dry["id"], "trip_no": 1}).json()
    assert r["ok"] is False and "refrigerated" in r["message"].lower()

    r = client.post(f"/api/dispatch/plans/{plan['plan_id']}/publish")
    assert r.status_code == 200, r.text

    # 4. loader loads VEH057 in reverse stop order, flags a shortfall, gets a decision, releases
    login(client, "kasun@waypoint.lk", code)
    q = client.get("/api/dock/queue").json()
    trip = next(t for t in q["trips"] if t["vehicle"] == "VEH057")
    detail = client.get(f"/api/dock/trips/{trip['id']}").json()
    lines = [ln for v in detail["load_order"] for ln in v["lines"]]
    flagged = lines[-1]
    for ln in lines[:-1]:
        assert client.post(f"/api/dock/lines/{ln['id']}/check", json={"loaded": True}).status_code == 200
    r = client.post(f"/api/dock/lines/{flagged['id']}/flag", json={"kind": "missing", "qty": 3})
    assert r.status_code == 200, r.text
    issue_id = r.json()["issue_id"]
    r = client.post(f"/api/dock/trips/{trip['id']}/release", json={"temp_c": 3, "seal": "KH-58213"})
    assert r.status_code == 409 and "dispatcher" in r.json()["detail"]

    login(client, "nirosha@waypoint.lk", code)
    assert client.post(f"/api/dispatch/issues/{issue_id}/decide", json={"decision": "partial"}).status_code == 200

    login(client, "kasun@waypoint.lk", code)
    r = client.post(f"/api/dock/trips/{trip['id']}/release", json={"temp_c": 3, "seal": "KH-58213"})
    assert r.status_code == 200, r.text

    # 5. driver: start, first visit online, the rest offline with device times
    from relay_api.domain.handover import pin_proof
    from relay_api.seed import STORE_PINS

    login(client, "sunil@waypoint.lk", code)
    run = client.get("/api/driver/run").json()
    t = run["trips"][0]
    assert t["status"] == "loaded"
    visits = t["visits"]
    first, rest = visits[0], visits[1:]
    fathima = next(v for v in visits if v["outlet"] == "OUT105")
    assert fathima["pin_required"] and fathima["pin_check"]

    def handover(v):
        """Fathima types her PIN on the phone, wherever her store falls on the run; other stores give a name."""
        if v["outlet"] == "OUT105":
            return {"visit_id": v["visit_id"], "pin_proof": pin_proof(v["visit_id"], STORE_PINS["fathima@waypoint.lk"])}
        return {"receiver": "Staff"}

    batch = [
        ev("run.start", {"trip_id": t["id"]}),
        ev("stop.arrive", {"stop_ids": first["stop_ids"]}),
        ev("stop.complete", {"stop_ids": first["stop_ids"], "outcome": "partial", "photo": PNG, **handover(first)}),
    ]
    r = client.post("/api/sync", json={"device_id": "test", "events": batch}).json()
    assert [x["status"] for x in r["results"]] == ["applied"] * 3, r
    # resend: idempotent
    r = client.post("/api/sync", json={"device_id": "test", "events": batch}).json()
    assert [x["status"] for x in r["results"]] == ["duplicate"] * 3

    # the van goes dark: no heartbeat for longer than the threshold
    from relay_api.domain.simulate import advance
    from relay_api.models import DeviceSeen, Trip, User, Workspace

    u = db_session.query(User).filter_by(email="sunil@waypoint.lk").one()
    ws = db_session.query(Workspace).filter_by(code=code).one()
    ds = db_session.get(DeviceSeen, (ws.id, u.id))
    ds.last_seen_real = datetime.now(UTC) - timedelta(minutes=5)
    db_session.commit()
    advance(db_session, ws)
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(Trip, t["id"]).signal == "dark"

    # dispatcher queues a move for the last visit while the van is dark
    login(client, "nirosha@waypoint.lk", code)
    last = rest[-1]
    order_id = last_order = None
    snap = client.get("/api/dispatch/snapshot").json()
    last_order = next(o for o in snap["orders"] if o["trip"] == t["id"] and o["outlet"] == last["outlet"])
    opts = client.get("/api/dispatch/moves/options", params={"order_id": last_order["id"]}).json()
    target = next((o for o in opts if o["ok"]), None)
    if target:
        r = client.post(
            "/api/dispatch/moves", json={"order_id": last_order["id"], "vehicle_id": target["vehicle"], "trip_no": target["trip_no"]}
        ).json()
        assert r["ok"] and r.get("queued"), r
    order_id = last_order["id"]

    # the phone reconnects and syncs what it recorded offline: physical facts win
    login(client, "sunil@waypoint.lk", code)
    base = datetime.fromisoformat(first["eta"]) + timedelta(minutes=30)
    offline = []
    sent = {}
    for i, v in enumerate(rest):
        at = (base + timedelta(minutes=25 * i)).isoformat()
        sent[v["visit_id"]] = at
        offline += [
            ev("stop.arrive", {"stop_ids": v["stop_ids"]}, at, True),
            ev("stop.complete", {"stop_ids": v["stop_ids"], "outcome": "delivered", **handover(v)}, at, True),
        ]
    r = client.post("/api/sync", json={"device_id": "test", "events": offline}).json()
    assert all(x["status"] == "applied" for x in r["results"]), r
    run = client.get("/api/driver/run").json()
    assert all(v["status"] in ("delivered", "partial") for v in run["trips"][0]["visits"])
    # each record keeps the time it happened on the phone, not the time it reached the server
    for v in run["trips"][0]["visits"]:
        if v["visit_id"] in sent:
            assert datetime.fromisoformat(v["completed_at"]) == datetime.fromisoformat(sent[v["visit_id"]]), v["name"]

    login(client, "nirosha@waypoint.lk", code)
    snap = client.get("/api/dispatch/snapshot").json()
    if target:
        pm = next(p for p in snap["pending_moves"] if p["order_id"] == order_id)
        assert pm["status"] == "cancelled" and "delivered" in pm["resolution"]
    o = next(o for o in snap["orders"] if o["id"] == order_id)
    assert o["status"] == "delivered" and o["proof"]["offline"] is True

    # 6. store confirms receipt and reports a damaged line
    login(client, "fathima@waypoint.lk", code)
    home = client.get("/api/store/home").json()
    mine = next(o for o in home["deliveries"] if o["status"] in ("partial", "delivered") and o["vehicle"] == "VEH057")
    # confirmed with her PIN at the door, so the receiver is the store manager herself
    assert mine["proof"]["pin_verified"] is True
    assert mine["proof"]["receiver"] == "Fathima Rizwan"
    line = mine["lines"][0]
    r = client.post(
        f"/api/store/orders/{mine['id']}/receipt",
        json={"lines": [{"line_id": line["id"], "received_qty": line["qty"] - 1, "issue": "damaged"}], "note": "1 case crushed"},
    )
    assert r.status_code == 200 and r.json()["issues"] == 1

    login(client, "nirosha@waypoint.lk", code)
    snap = client.get("/api/dispatch/snapshot").json()
    assert any(i["kind"] == "receipt_issue" for i in snap["issues"])


@pytest.mark.usefixtures("db_ready")
def test_sync_keeps_applied_events_when_a_later_one_fails(client, sandbox_code):
    """One bad record in a batch must not undo the good ones before it, and a refused record stays
    refused when the phone sends it again (it is never silently marked as synced)."""
    login(client, "nirosha@waypoint.lk", sandbox_code)
    assert client.post("/api/dispatch/plans", json={"policy": "balanced", "publish": True}).status_code == 200
    login(client, "kasun@waypoint.lk", sandbox_code)
    q = client.get("/api/dock/queue").json()
    trip = next(t for t in q["trips"] if t["vehicle"] == "VEH057")
    detail = client.get(f"/api/dock/trips/{trip['id']}").json()
    line = detail["load_order"][0]["lines"][0]
    good = ev("line.check", {"line_id": line["id"], "loaded": True})
    bad = ev("line.check", {"line_id": 999_999_999, "loaded": True})
    r = client.post("/api/sync", json={"device_id": "dock-tablet", "events": [good, bad]}).json()
    assert [x["status"] for x in r["results"]] == ["applied", "rejected"], r
    detail = client.get(f"/api/dock/trips/{trip['id']}").json()
    assert next(ln for v in detail["load_order"] for ln in v["lines"] if ln["id"] == line["id"])["load_state"] == "loaded"
    again = client.post("/api/sync", json={"device_id": "dock-tablet", "events": [good, bad]}).json()
    assert [x["status"] for x in again["results"]] == ["duplicate", "rejected"], again
    assert client.post("/api/sync", json={"device_id": "x" * 33, "events": []}).status_code == 422
