"""Runtime configuration (12-factor: environment variables, optional .env at the repo root)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(REPO_ROOT / ".env",), env_file_encoding="utf-8", extra="ignore")

    env: str = "development"
    database_url: str = "postgresql+psycopg://relay:relay@localhost:5433/relay"
    secret_key: str = "dev-only-secret-change-me-0123456789abcdef"
    cookie_name: str = "__session"
    cookie_secure: bool = False
    session_hours: int = 12

    data_dir: Path = REPO_ROOT / "data"
    web_dist: Path = REPO_ROOT / "apps" / "web" / "dist"
    seed_on_start: bool = True
    demo_password: str = "relay2026"
    allow_sandboxes: bool = True
    max_sandboxes: int = 50
    reset_token: str = ""  # optional: lets a scheduler call POST /api/demo/reset without a session

    solver_time_limit_s: float = 5.0
    explain_budget_s: float = 4.0
    heartbeat_dark_s: int = 45
    sim_tick_s: float = 4.0
    max_media_bytes: int = 2_000_000
    log_level: str = "INFO"

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
