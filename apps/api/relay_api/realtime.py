"""Live updates: Postgres LISTEN/NOTIFY fanned out to browsers over Server-Sent Events.

One listener thread per process holds a dedicated connection and LISTENs on the channel that
`events.notify()` writes to. Each browser stream registers its workspace and topic interests;
matching notifications are pushed to it as `invalidate` events and the browser refetches.

While at least one browser watches a workspace, a ticker advances the simulated part of the
network (and detects vans that went dark). Running it inside the streaming request keeps CPU
allocated on Cloud Run without needing an always-on worker.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field

import psycopg

from .config import get_settings
from .db import SessionLocal, libpq_dsn
from .domain.events import CHANNEL

log = logging.getLogger("relay.realtime")


@dataclass(eq=False)
class Subscriber:
    ws: str
    interests: set[str]
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=200))
    loop: asyncio.AbstractEventLoop | None = None

    def wants(self, topics: list[str]) -> bool:
        if "*" in self.interests:
            return True
        for t in topics:
            if t in self.interests:
                return True
            if t.endswith(":*") and any(i.startswith(t[:-1]) for i in self.interests):
                return True
        return False


class Hub:
    def __init__(self) -> None:
        self.subs: set[Subscriber] = set()
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.stop = threading.Event()
        self.tickers: dict[str, asyncio.Task] = {}

    # ---------------------------------------------------------------- listener thread
    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop.clear()
        self.thread = threading.Thread(target=self._listen, name="relay-listen", daemon=True)
        self.thread.start()

    def shutdown(self) -> None:
        self.stop.set()

    def _listen(self) -> None:
        while not self.stop.is_set():
            try:
                with psycopg.connect(libpq_dsn(), autocommit=True) as conn:
                    conn.execute(f"LISTEN {CHANNEL}")
                    log.info("listening on %s", CHANNEL)
                    while not self.stop.is_set():
                        for n in conn.notifies(timeout=5.0):
                            self._dispatch(n.payload)
            except Exception as exc:  # pragma: no cover - reconnect loop
                log.warning("listener error: %s; reconnecting", exc)
                time.sleep(2)

    def _dispatch(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        with self.lock:
            targets = [s for s in self.subs if s.ws == msg.get("ws") and s.wants(msg.get("topics", []))]
        for s in targets:
            if s.loop:
                s.loop.call_soon_threadsafe(_put, s.queue, msg)

    # ---------------------------------------------------------------- subscribers
    def add(self, sub: Subscriber) -> None:
        with self.lock:
            self.subs.add(sub)
        if sub.ws not in self.tickers or self.tickers[sub.ws].done():
            self.tickers[sub.ws] = asyncio.get_running_loop().create_task(self._tick(sub.ws))

    def remove(self, sub: Subscriber) -> None:
        with self.lock:
            self.subs.discard(sub)

    def watching(self, ws: str) -> bool:
        with self.lock:
            return any(s.ws == ws for s in self.subs)

    async def _tick(self, ws_id: str) -> None:
        from .domain.simulate import advance
        from .models import Workspace

        period = get_settings().sim_tick_s

        def run_once() -> None:
            with SessionLocal() as db:
                ws = db.get(Workspace, ws_id)
                if ws:
                    advance(db, ws)
                    db.commit()

        while self.watching(ws_id):
            try:
                await asyncio.to_thread(run_once)
            except Exception as exc:  # pragma: no cover
                log.warning("simulation tick failed: %s", exc)
            await asyncio.sleep(period)


def _put(q: asyncio.Queue, msg: dict) -> None:
    with contextlib.suppress(asyncio.QueueFull):
        q.put_nowait(msg)


hub = Hub()
