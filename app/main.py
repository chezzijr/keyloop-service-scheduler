from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import make_engine
from app.routers import health
from app.settings import Settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings.from_env()
    app.state.engine = make_engine(
        settings.database_url,
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        statement_timeout_ms=settings.statement_timeout_ms,
    )
    yield
    await app.state.engine.dispose()


app = FastAPI(title="Unified Service Scheduler", lifespan=lifespan)
app.include_router(health.router)
