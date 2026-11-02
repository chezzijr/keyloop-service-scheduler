"""Database engine and the per-request session dependency."""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.settings import Settings


def make_engine(
    url: str,
    *,
    pool_size: int = Settings.pool_size,
    max_overflow: int = Settings.max_overflow,
    statement_timeout_ms: int = Settings.statement_timeout_ms,
) -> AsyncEngine:
    return create_async_engine(
        url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        # asyncpg sends these on connect, so every query has the server-side timeout
        connect_args={"server_settings": {"statement_timeout": str(statement_timeout_ms)}},
    )


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(request.app.state.engine, expire_on_commit=False) as session:
        yield session
