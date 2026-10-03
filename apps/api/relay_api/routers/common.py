"""Shared endpoints: health, clock, reference data, media."""

from __future__ import annotations

import hashlib
import json
import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Ctx, get_ctx
from ..domain.clock import clock_payload
from ..domain.eta import RefCache
from ..models import Depot, Media, Product, Workspace

router = APIRouter(prefix="/api", tags=["common"])


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    from relay_ml import get_predictor

    try:
        db.execute(text("select 1"))
        seeded = db.get(Workspace, "main") is not None
        db_ok = "ok"
    except Exception as exc:  # pragma: no cover
        db_ok, seeded = f"error: {exc.__class__.__name__}", False
    return {
        "status": "ok" if db_ok == "ok" else "degraded",
        "db": db_ok,
        "seeded": seeded,
        "predictor": get_predictor().kind,
        "version": "1.0.0",
        # which build is answering: Cloud Run sets K_REVISION; deploy.sh sets GIT_SHA
        "revision": os.environ.get("K_REVISION", "local"),
        "commit": os.environ.get("GIT_SHA", ""),
    }


@router.get("/clock")
def clock(ctx: Ctx = Depends(get_ctx)) -> dict:
    return clock_payload(ctx.ws)


@router.get("/reference")
def reference(request: Request, response: Response, ctx: Ctx = Depends(get_ctx)) -> dict:
    ref = RefCache.load(ctx.db)
    body = {
        "depots": [{"id": d.id, "code": d.code, "name": d.name, "kind": d.kind} for d in ctx.db.query(Depot)],
        "districts": [
            {
                "name": d.name,
                "depot": d.depot,
                "out": d.d2d_min,
                "inter": d.inter_min,
                "km": d.d2d_km,
                "ikm": d.inter_km,
                "road": d.road_class,
            }
            for d in ref.districts.values()
        ],
        "outlets": [
            {
                "id": o.id,
                "name": o.name,
                "brand": o.brand,
                "district": o.district,
                "depot": o.depot,
                "dock": o.dock_type,
                "parking": o.parking,
                "mall_window": o.mall_window,
                "open": o.window_open,
                "close": o.window_close,
                "manager": o.manager,
            }
            for o in ref.outlets.values()
        ],
        "allowance": [{"brand": b, "dock": d, "minutes": m} for (b, d), m in ref.allowance.items()],
        "products": [
            {"sku": p.sku, "name": p.name, "brand": p.brand, "temp": p.temp, "uom": p.uom}
            for p in ctx.db.query(Product).order_by(Product.sku)
        ],
        "budgets": {"fresh": 270, "day": 480, "max_trips": 2},
    }
    etag = hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
    if request.headers.get("if-none-match") == etag:
        raise HTTPException(304)
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, max-age=300"
    return body


@router.get("/media/{media_id}")
def media(media_id: str, ctx: Ctx = Depends(get_ctx)) -> Response:
    m = ctx.db.get(Media, media_id)
    if not m or m.workspace_id != ctx.ws.id:
        raise HTTPException(404, "Not found")
    return Response(content=m.data, media_type=m.content_type, headers={"Cache-Control": "private, max-age=86400"})
