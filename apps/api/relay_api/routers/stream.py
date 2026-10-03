"""GET /api/stream: Server-Sent Events for the signed-in user's workspace and scope."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from ..deps import Ctx, get_ctx
from ..domain.clock import clock_payload
from ..models import Workspace
from ..realtime import Subscriber, hub

router = APIRouter(prefix="/api", tags=["live"])


@router.get("/stream")
async def stream(request: Request, ctx: Ctx = Depends(get_ctx)) -> StreamingResponse:
    user, ws_id = ctx.user, ctx.ws.id
    interests = {"plan", "clock"} | set(ctx.topic_scope())
    if user.role == "dispatcher":
        interests = {"*"}
    ctx.db.close()
    sub = Subscriber(ws=ws_id, interests=interests)
    sub.loop = asyncio.get_running_loop()
    hub.add(sub)

    async def gen():
        try:
            yield "retry: 3000\n\n"
            yield f"event: hello\ndata: {json.dumps({'ws': ws_id, 'interests': sorted(interests)})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(sub.queue.get(), timeout=15)
                    batch = [msg]
                    await asyncio.sleep(0.25)  # coalesce bursts (a plan publish notifies many topics)
                    while not sub.queue.empty():
                        batch.append(sub.queue.get_nowait())
                    topics = sorted({t for m in batch for t in m.get("topics", [])})
                    kinds = sorted({m.get("kind", "change") for m in batch})
                    yield f"event: invalidate\ndata: {json.dumps({'topics': topics, 'kinds': kinds})}\n\n"
                except TimeoutError:
                    from ..db import SessionLocal

                    with SessionLocal() as db:
                        ws = db.get(Workspace, ws_id)
                        if ws:
                            yield f"event: clock\ndata: {json.dumps(clock_payload(ws))}\n\n"
        finally:
            hub.remove(sub)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
