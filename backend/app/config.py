"""Runtime configuration, loaded from the environment.

The Parallel API key is read from PARALLEL_API_KEY and never leaves the server.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Parallel Web Systems credentials / SDK options.
    parallel_api_key: str = ""
    parallel_base_url: str | None = None
    # Task processor — "core" is reliable up to ~10 output fields.
    parallel_task_processor: str = "core"

    # How many results we request per search query. Doubles as the rank denominator.
    results_per_query: int = 10

    # CORS origin for the React dev server.
    frontend_origin: str = "http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()
