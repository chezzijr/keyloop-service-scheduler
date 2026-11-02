"""Shared fixtures: one postgres:18 container per test session, migrated once with
Alembic, an engine on it, and an in-process HTTP client. httpx ASGITransport does not
run the app lifespan, so the tests put their own engine on app.state.

Every test that uses the database (through `engine`, `session` or `client`) starts on
empty tables with identities reset. Tests that do not touch the database start no
container."""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from testcontainers.community.postgres import PostgresContainer

from app.db import make_engine
from app.main import app


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    with PostgresContainer("postgres:18", driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest.fixture(scope="session")
def migrated_url(database_url: str) -> str:
    # sync fixture: env.py calls asyncio.run(), which fails inside a running event loop
    config = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return database_url


@pytest.fixture(scope="session")
async def session_engine(migrated_url: str) -> AsyncIterator[AsyncEngine]:
    eng = make_engine(migrated_url)
    yield eng
    await eng.dispose()


@pytest.fixture
async def engine(session_engine: AsyncEngine) -> AsyncEngine:
    async with session_engine.begin() as conn:
        tables = await conn.scalar(
            text(
                "SELECT string_agg(quote_ident(tablename), ', ') FROM pg_tables"
                " WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        )
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    return session_engine


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False) as s:
        yield s


@pytest.fixture
async def client(engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    app.state.engine = engine
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
