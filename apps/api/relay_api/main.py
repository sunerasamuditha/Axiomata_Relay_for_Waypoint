"""Relay API application: FastAPI under /api, and the built React app for every other path."""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .realtime import hub
from .routers import auth, common, demo, dispatch, field, stream

settings = get_settings()
logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("relay")
if settings.is_production and (settings.secret_key.startswith("dev-only") or len(settings.secret_key) < 32):
    # refuse to sign sessions with the public development key
    raise RuntimeError("SECRET_KEY must be set to a long random value when ENV=production")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.seed_on_start:
        from .db import SessionLocal
        from .seed import ensure_seeded_locked

        try:
            with SessionLocal() as db:
                log.info("seed: %s", ensure_seeded_locked(db))
        except Exception as exc:  # pragma: no cover - surfaced by /api/health
            log.error("seeding failed: %s", exc)
    hub.start()
    yield
    hub.shutdown()


app = FastAPI(
    title="Relay API",
    version="1.0.0",
    description="Waypoint delivery planning: one plan, four faces (dispatcher, loader, driver, store manager).",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)


@app.middleware("http")
async def request_log(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("unhandled error rid=%s path=%s", rid, request.url.path)
        response = JSONResponse({"detail": "Something went wrong on our side. It has been logged.", "request_id": rid}, status_code=500)
    ms = (time.perf_counter() - t0) * 1000
    if request.url.path.startswith("/api") and request.url.path != "/api/stream":
        log.info(
            "rid=%s %s %s %s %.0fms role=%s ws=%s",
            rid,
            request.method,
            request.url.path,
            response.status_code,
            ms,
            getattr(request.state, "role", "-"),
            getattr(request.state, "ws", "-"),
        )
    response.headers["X-Request-Id"] = rid
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


for r in (auth.router, common.router, dispatch.router, field.router, demo.router, stream.router):
    app.include_router(r)


# ---- the single-page app (built by Vite into apps/web/dist) ----
dist = settings.web_dist
if (dist / "index.html").exists():
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets", check_dir=False), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        f = dist / path
        if path and f.is_file() and dist in f.resolve().parents:
            headers = {"Cache-Control": "no-cache"} if f.name in ("sw.js", "manifest.webmanifest", "registerSW.js") else {}
            return FileResponse(f, headers=headers)
        return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})
