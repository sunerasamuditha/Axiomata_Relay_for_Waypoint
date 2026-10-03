"""Request dependencies: the signed-in user, their workspace, role guards and CSRF protection."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import User, Workspace
from .security import read_token

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass
class Ctx:
    user: User
    ws: Workspace
    db: Session

    @property
    def actor(self) -> str:
        return self.user.short_name

    def topic_scope(self) -> list[str]:
        u = self.user
        if u.role == "loader" and u.depot:
            return [f"depot:{u.depot}"]
        if u.role == "driver" and u.vehicle_id:
            return [f"vehicle:{u.vehicle_id}"]
        if u.role == "store" and u.outlet_id:
            return [f"outlet:{u.outlet_id}"]
        return ["dispatch"]


def csrf_guard(request: Request) -> None:
    """Writes must come from our own front end: same-site cookie + a custom header that a
    cross-site form or image cannot set."""
    if request.method not in SAFE_METHODS and request.headers.get("x-relay-client") != "web":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing X-Relay-Client header")


def get_ctx(request: Request, db: Session = Depends(get_db)) -> Ctx:
    token = request.cookies.get(get_settings().cookie_name)
    claims = read_token(token) if token else None
    if not claims:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    user = db.get(User, int(claims["sub"]))
    ws = db.get(Workspace, claims.get("ws") or "main")
    if not user or not ws:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    csrf_guard(request)
    request.state.user = user.email
    request.state.role = user.role
    request.state.ws = ws.id
    return Ctx(user=user, ws=ws, db=db)


def require(*roles: str):
    def dep(ctx: Ctx = Depends(get_ctx)) -> Ctx:
        if ctx.user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"This needs the {' or '.join(roles)} role")
        return ctx

    return dep


dispatcher = require("dispatcher")
loader = require("loader")
driver = require("driver")
store = require("store")
