"""API test fixtures. They need a reachable Postgres (DATABASE_URL); `make test` starts one with
Docker Compose, and CI provides a service container. Without a database the API tests skip."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

os.environ.setdefault("SEED_ON_START", "false")
os.environ.setdefault("SOLVER_TIME_LIMIT_S", "3")
os.environ.setdefault("EXPLAIN_BUDGET_S", "2")


@pytest.fixture(scope="session")
def db_ready():
    from alembic import command
    from alembic.config import Config
    from relay_api.config import REPO_ROOT
    from relay_api.db import SessionLocal

    try:
        with SessionLocal() as db:
            db.execute(text("select 1"))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"no database: {exc}")
    cfg = Config(str(REPO_ROOT / "apps" / "api" / "alembic.ini"))
    command.upgrade(cfg, "head")
    from relay_api.seed import ensure_seeded

    with SessionLocal() as db:
        ensure_seeded(db)
    return True


@pytest.fixture()
def db_session(db_ready):
    from relay_api.db import SessionLocal

    with SessionLocal() as db:
        yield db


@pytest.fixture()
def sandbox_code(db_ready):
    from relay_api.db import SessionLocal
    from relay_api.models import Workspace
    from relay_api.seed import create_workspace, new_sandbox_code, wipe_workspace

    code = new_sandbox_code()
    ws_id = "ws_test_" + code[-4:].lower()
    with SessionLocal() as db:
        create_workspace(db, ws_id, "Test", code, sandbox=True)
        db.commit()
    yield code
    with SessionLocal() as db:
        wipe_workspace(db, ws_id)
        ws = db.get(Workspace, ws_id)
        if ws:
            db.delete(ws)
        db.commit()


@pytest.fixture()
def client(db_ready):
    from fastapi.testclient import TestClient
    from relay_api.main import app

    with TestClient(app, headers={"x-relay-client": "web"}) as c:
        yield c
