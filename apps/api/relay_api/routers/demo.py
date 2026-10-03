"""Demo controls: the virtual clock, reset, private sandboxes for judges."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import Ctx, csrf_guard, dispatcher, get_ctx
from ..domain import clock as clk
from ..domain.eta import RefCache
from ..domain.events import notify, record
from ..models import Workspace
from ..seed import create_workspace, new_sandbox_code

router = APIRouter(prefix="/api/demo", tags=["demo"])


class ClockIn(BaseModel):
    op: str = Field(pattern="^(advance|set|run|pause)$")
    minutes: float | None = Field(default=None, ge=0, le=24 * 60)
    at: datetime | None = None
    rate: float | None = Field(default=None, ge=0, le=600)


@router.post("/clock")
def clock(body: ClockIn, ctx: Ctx = Depends(get_ctx)) -> dict:
    if ctx.user.role not in ("dispatcher",):
        raise HTTPException(403, "Only the dispatcher can change the demo clock.")
    ws = ctx.ws
    if body.op == "advance":
        clk.advance_by(ws, body.minutes or 15)
    elif body.op == "set" and body.at:
        clk.advance_to(ws, body.at)
    elif body.op == "run":
        clk.set_running(ws, body.rate or 1.0)
    elif body.op == "pause":
        clk.set_running(ws, None)
    record(
        ctx.db,
        ws,
        "clock.changed",
        actor_name=ctx.actor,
        actor_role=ctx.user.role,
        payload=body.model_dump(mode="json"),
        topics=["clock", "plan"],
    )
    notify(ctx.db, ws.id, ["clock", "plan", "depot:Kandy", "depot:Peliyagoda", "vehicle:*", "outlet:*"])
    ctx.db.commit()
    return clk.clock_payload(ws)


@router.post("/reset")
def reset(request: Request, db: Session = Depends(get_db), x_reset_token: str | None = Header(default=None)) -> dict:
    s = get_settings()
    if s.reset_token and x_reset_token == s.reset_token:
        ws_id = "main"
    else:
        ctx = dispatcher(get_ctx(request, db))
        ws_id = ctx.ws.id
    ws = db.get(Workspace, ws_id)
    create_workspace(db, ws_id, ws.name, ws.code, sandbox=ws.sandbox)
    notify(db, ws_id, ["dispatch", "plan", "clock", "depot:Kandy", "depot:Peliyagoda", "vehicle:*", "outlet:*"], kind="reset")
    db.commit()
    return {"ok": True, "workspace": ws_id}


@router.post("/sandboxes")
def sandbox(request: Request, db: Session = Depends(get_db)) -> dict:
    """A private copy of the demo day. Sign in to every role with the code it returns."""
    csrf_guard(request)
    s = get_settings()
    if not s.allow_sandboxes:
        raise HTTPException(403, "Sandboxes are disabled on this deployment.")
    n = db.scalar(select(func.count()).select_from(Workspace).where(Workspace.sandbox.is_(True))) or 0
    if n >= s.max_sandboxes:
        oldest = db.scalars(select(Workspace).where(Workspace.sandbox.is_(True)).order_by(Workspace.created_at)).first()
        from ..seed import wipe_workspace

        wipe_workspace(db, oldest.id)
        db.delete(oldest)
        db.flush()
    RefCache.load(db)
    code = new_sandbox_code()
    ws = create_workspace(db, "ws_" + code.split("-")[1].lower(), f"Sandbox {code}", code, sandbox=True)
    db.commit()
    return {"code": ws.code, "workspace": ws.id}


@router.get("/info")
def info(ctx: Ctx = Depends(get_ctx)) -> dict:
    return {"clock": clk.clock_payload(ctx.ws), "workspace": {"id": ctx.ws.id, "code": ctx.ws.code, "sandbox": ctx.ws.sandbox}}
