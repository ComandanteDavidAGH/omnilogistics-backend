"""Configuración centralizada. Todo se controla por variables de entorno."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

ENGINE_VERSION = "1.0.0"


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    database_url: str
    admin_api_key: str
    allowed_origins: tuple
    max_upload_mb: int
    max_rows: int
    max_columns: int
    rate_limit_per_minute: int
    log_level: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    url = os.getenv("DATABASE_URL", "sqlite:///./genesis_b2b.db")
    # Render/Heroku entregan "postgres://"; SQLAlchemy necesita el driver explícito.
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)

    origins = tuple(
        o.strip()
        for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
        if o.strip()
    )
    return Settings(
        database_url=url,
        admin_api_key=os.getenv("ADMIN_API_KEY", ""),
        allowed_origins=origins,
        max_upload_mb=_int("MAX_UPLOAD_MB", 20),
        max_rows=_int("MAX_ROWS", 200_000),
        max_columns=_int("MAX_COLUMNS", 200),
        rate_limit_per_minute=_int("RATE_LIMIT_PER_MINUTE", 30),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
