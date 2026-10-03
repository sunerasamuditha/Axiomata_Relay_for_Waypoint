"""One sign-in portal for every role. The role comes from the account, never from the UI."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import Ctx, csrf_guard, get_ctx
from ..models import User, Workspace
from ..security import make_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

HOME = {"dispatcher": "/dispatch", "loader": "/dock", "driver": "/driver", "store": "/store"}


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=1, max_length=200)
    workspace: str | None = Field(default=None, max_length=16, description="Sandbox code, e.g. RLY-7K2Q. Empty = shared demo.")


def user_payload(u: User, ws: Workspace) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "name": u.name,
        "short_name": u.short_name,
        "initials": u.initials,
        "color": u.color,
        "role": u.role,
        "title": u.title,
        "depot": u.depot,
        "dock": u.dock,
        "vehicle_id": u.vehicle_id,
        "outlet_id": u.outlet_id,
        "lang": u.lang,
        "home": HOME[u.role],
        "workspace": {"id": ws.id, "code": ws.code, "name": ws.name, "sandbox": ws.sandbox},
    }


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    csrf_guard(request)
    u = db.scalar(select(User).where(func.lower(User.email) == body.email.strip().lower()))
    if not u or not verify_password(body.password, u.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That email and password don't match a Relay account.")
    code = (body.workspace or "").strip().upper()
    ws = db.scalar(select(Workspace).where(Workspace.code == code)) if code else db.get(Workspace, "main")
    if not ws:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No demo workspace with code {code}.")
    s = get_settings()
    response.set_cookie(
        s.cookie_name,
        make_token(u.id, u.role, ws.id),
        httponly=True,
        secure=s.cookie_secure,
        samesite="lax",
        max_age=s.session_hours * 3600,
        path="/",
    )
    return user_payload(u, ws)


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    csrf_guard(request)
    response.delete_cookie(get_settings().cookie_name, path="/")
    return {"ok": True}


@router.get("/me")
def me(ctx: Ctx = Depends(get_ctx)) -> dict:
    return user_payload(ctx.user, ctx.ws)


class PrefsIn(BaseModel):
    lang: str | None = Field(default=None, pattern="^(en|si|ta)$")


@router.patch("/me")
def update_me(body: PrefsIn, ctx: Ctx = Depends(get_ctx)) -> dict:
    """Personal preferences that follow the account to any device (today: the driver's language)."""
    if body.lang:
        ctx.user.lang = body.lang
    ctx.db.commit()
    return user_payload(ctx.user, ctx.ws)
