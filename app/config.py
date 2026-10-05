"""Settings, read from environment variables or a .env file (see .env.example)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), env_prefix="PP_", extra="ignore")

    # SQLite file by default (zero setup on a laptop); set PP_DATABASE_URL for PostgreSQL, e.g.
    # postgresql+psycopg://profitpilot:profitpilot@localhost:5432/profitpilot
    database_url: str = f"sqlite:///{(ROOT / 'profitpilot.db').as_posix()}"
    seed_demo: bool = True                 # create the demo seller and 5 products on first start
    serve_frontend: bool = True            # serve frontend/index.html at /
    frontend_dir: str = str(ROOT / "frontend")
    cors_origins: List[str] = ["*"]
    api_key: str = ""                      # if set, write endpoints need the X-API-Key header
    jobs_interval_sec: int = 60            # background loop for due checks; 0 disables it
    demo_clock: bool = True                # allow /admin/clock/advance (time travel for demos)
    use_learned_models: bool = False       # use models/elasticity.json for β instead of the −3 prior
    env: str = "dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()
