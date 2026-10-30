"""ICE (STUN/TURN) server list for WebRTC calls.

Handed out by the backend so TURN credentials live in the host's environment instead of
being baked into the APK. Without a TURN relay, two phones on different networks often
cannot reach each other (symmetric NAT / carrier NAT) and the call connects with no media.
"""

import logging
import uuid

import httpx
from fastapi import APIRouter, Depends

from app.api.deps import get_current_user_id
from app.core.config import settings

logger = logging.getLogger(__name__)
# httpx logs every request URL at INFO, and Metered takes the API key in the query string --
# keep it out of the logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
router = APIRouter(prefix="/api/v1", tags=["ice"])

STUN_ONLY = [{"urls": ["stun:stun.l.google.com:19302", "stun:stun1.l.google.com:19302"]}]


@router.get("/ice-servers")
async def get_ice_servers(_: uuid.UUID = Depends(get_current_user_id)) -> dict:
    if not (settings.metered_app_name and settings.metered_api_key):
        return {"ice_servers": STUN_ONLY, "turn": False}
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            r = await client.get(
                f"https://{settings.metered_app_name}.metered.live/api/v1/turn/credentials",
                params={"apiKey": settings.metered_api_key},
            )
            r.raise_for_status()
            servers = r.json()
        if isinstance(servers, list) and servers:
            return {"ice_servers": STUN_ONLY + servers, "turn": True}
    except httpx.HTTPStatusError as e:
        # Deliberately not logger.exception: the exception text contains the URL (and key).
        logger.error("Metered rejected the TURN credentials request: HTTP %s; falling back to STUN only", e.response.status_code)
    except Exception as e:
        logger.error("could not fetch TURN credentials (%s); falling back to STUN only", type(e).__name__)
    return {"ice_servers": STUN_ONLY, "turn": False}
