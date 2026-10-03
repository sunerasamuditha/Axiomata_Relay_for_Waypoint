"""The event log and live-update fan-out.

`record()` appends an Event row and queues a Postgres NOTIFY in the same transaction, so a
notification is delivered if and only if the change commits. Each notification names *topics*;
browsers subscribe by role and scope and refetch what changed (SSE as an invalidation signal,
REST as the source of truth).

Topics
  plan            the published plan changed (everyone)
  dispatch        anything the dispatcher's canvas and feed show
  depot:<id>      a dock's queue (Kandy, Peliyagoda)
  vehicle:<id>    a driver's run
  outlet:<id>     a store's deliveries and notices
  clock           the virtual clock moved
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..models import Event, Notice, Workspace
from .clock import now_virtual, utcnow

CHANNEL = "relay_events"


def notify(db: Session, ws_id: str, topics: list[str], kind: str = "change") -> None:
    payload = json.dumps({"ws": ws_id, "topics": sorted(set(topics)), "kind": kind})
    db.execute(text("select pg_notify(:c, :p)"), {"c": CHANNEL, "p": payload})


def record(
    db: Session,
    ws: Workspace,
    type: str,
    *,
    actor_name: str = "",
    actor_role: str = "",
    entity: str = "",
    payload: dict | None = None,
    topics: list[str] | None = None,
    client_event_id: str | None = None,
    device_at: datetime | None = None,
    result: dict | None = None,
) -> Event:
    ev = Event(
        workspace_id=ws.id,
        type=type,
        actor_name=actor_name,
        actor_role=actor_role,
        entity=entity,
        payload=payload or {},
        virtual_at=now_virtual(ws),
        device_at=device_at,
        server_at=utcnow(),
        client_event_id=client_event_id,
        result=result or {},
    )
    db.add(ev)
    notify(db, ws.id, ["dispatch", *(topics or [])])
    return ev


def post_notice(
    db: Session,
    ws: Workspace,
    audience: str,
    title: str,
    body: str = "",
    *,
    kind: str = "blue",
    icon: str = "info",
    actions: list[list[str]] | None = None,
    key: str | None = None,
    entity: str | None = None,
    at: datetime | None = None,
) -> Notice:
    """Add a message to a face's feed. With `key`, an existing unresolved notice is replaced."""
    if key:
        old = db.query(Notice).filter_by(workspace_id=ws.id, audience=audience, key=key, resolved=False).all()
        for n in old:
            n.resolved = True
    n = Notice(
        workspace_id=ws.id,
        audience=audience,
        kind=kind,
        icon=icon,
        title=title,
        body=body,
        at=at or now_virtual(ws),
        actions=actions or [],
        key=key,
        entity=entity,
    )
    db.add(n)
    topic = {"dispatch": "dispatch"}.get(audience, audience)
    notify(db, ws.id, [topic, "dispatch"] if topic != "dispatch" else ["dispatch"])
    return n


def resolve_notices(db: Session, ws: Workspace, audience: str, key: str) -> None:
    for n in db.query(Notice).filter_by(workspace_id=ws.id, audience=audience, key=key, resolved=False):
        n.resolved = True
