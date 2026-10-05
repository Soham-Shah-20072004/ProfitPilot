"""Settings, read from environment variables or a .env file (see .env.example)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import AliasChoices, Field
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

    # ---- AI layer (Gemini). No key → every AI feature falls back to the engine rules / baseline models.
    # The key stays on the server; the browser never sees it.
    gemini_api_key: str = Field(default="", validation_alias=AliasChoices("PP_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"))
    gemini_model: str = "gemini-2.5-flash"          # any generateContent model, e.g. gemini-2.5-pro, gemini-3-flash-preview
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_timeout_sec: float = 30.0
    ai_enabled: bool = True                         # master switch; false = rules only even with a key
    ai_rate_per_min: int = 30                       # AI requests per seller per minute (simple in-memory limit)
    ai_max_tool_rounds: int = 6                     # coach: max function-calling rounds per question


@lru_cache
def get_settings() -> Settings:
    return Settings()
