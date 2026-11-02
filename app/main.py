from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import errors, observability
from app.db import make_engine
from app.routers import appointments, health
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


observability.configure()

app = FastAPI(title="Unified Service Scheduler", lifespan=lifespan)
app.add_middleware(observability.RequestMiddleware)
app.include_router(health.router)
app.include_router(appointments.router)
errors.register(app)
