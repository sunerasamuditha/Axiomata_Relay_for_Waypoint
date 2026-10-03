"""Database engine, sessions and the declarative base."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()
engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=10,
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=True, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def held_lock(key: int) -> Iterator[bool]:
    """Session-level advisory lock on its own pooled connection, held for the whole block even when
    the work inside commits (a Session hands its connection back to the pool on every commit, so a
    lock taken through the Session could be released on the wrong connection). Yields False when
    another process holds it."""
    with engine.connect() as conn:
        got = bool(conn.execute(text("select pg_try_advisory_lock(:k)"), {"k": key}).scalar())
        conn.commit()
        try:
            yield got
        finally:
            if got:
                conn.execute(text("select pg_advisory_unlock(:k)"), {"k": key})
                conn.commit()


def libpq_dsn() -> str:
    """Plain libpq connection string (for psycopg LISTEN), derived from DATABASE_URL."""
    url = make_url(_settings.database_url).set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


def advisory_lock(db: Session, key: int) -> bool:
    """Transaction-scoped advisory lock; False if another session holds it."""
    return bool(db.execute(text("select pg_try_advisory_xact_lock(:k)"), {"k": key}).scalar())
