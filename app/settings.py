"""Settings read from environment variables once, at startup."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    pool_size: int = 5
    max_overflow: int = 10
    statement_timeout_ms: int = 5000

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ["DATABASE_URL"],
            pool_size=int(os.environ.get("DB_POOL_SIZE", cls.pool_size)),
            max_overflow=int(os.environ.get("DB_MAX_OVERFLOW", cls.max_overflow)),
            statement_timeout_ms=int(
                os.environ.get("DB_STATEMENT_TIMEOUT_MS", cls.statement_timeout_ms)
            ),
        )
