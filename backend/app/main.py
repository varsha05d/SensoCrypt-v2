import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from app.api.auth import router as auth_router
from app.api.auth_phone import router as auth_phone_router
from app.api.calls import router as calls_router
from app.api.ice import router as ice_router
from app.api.session import router as session_router
from app.api.signal import router as signal_router
from app.api.telemetry import router as telemetry_router
from app.core.config import settings
from app.db.models import Base
from app.db.session import engine

logging.basicConfig(level=logging.INFO)


async def _warm_turn() -> None:
    from app.api.ice import ensure_credential

    try:
        await ensure_credential()
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("TURN credential warm-up failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Table creation directly, no migrations yet -- matches the original SensoCrypt's
    # approach; revisit with Alembic once the schema needs to evolve without dropping data.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all never alters an existing table; add columns introduced after first deploy.
        await conn.execute(text("ALTER TABLE calls ADD COLUMN IF NOT EXISTS failure_reason VARCHAR"))
    # Mint/renew the relay credential now, so it is already accepted by the time anyone calls
    # (a credential created seconds before a call is not yet usable at the relay).
    if settings.metered_app_name and settings.metered_api_key:
        asyncio.create_task(_warm_turn())
    yield


app = FastAPI(title="SensoCrypt v2", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(auth_phone_router)
app.include_router(calls_router)
app.include_router(ice_router)
app.include_router(session_router)
app.include_router(signal_router)
app.include_router(telemetry_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
