"""ICE (STUN/TURN) server list for WebRTC calls.

Handed out by the backend so TURN credentials live in the host's environment instead of
being baked into the APK. Without a TURN relay, two phones on different networks often
cannot reach each other (symmetric NAT / carrier NAT) and the call connects with no media.
"""

import asyncio
import logging
import uuid
import zlib
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends

from app.api.deps import get_current_user_id
from app.core.config import settings
from app.db.models import TurnCredential
from app.db.session import async_session

logger = logging.getLogger(__name__)
# httpx logs every request URL at INFO, and Metered takes the API key in the query string --
# keep it out of the logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
router = APIRouter(prefix="/api/v1", tags=["ice"])

STUN_ONLY = [{"urls": ["stun:stun.l.google.com:19302", "stun:stun1.l.google.com:19302"]}]


CREDENTIAL_TTL_S = 30 * 24 * 3600
RENEW_BEFORE_EXPIRY_S = 24 * 3600     # mint the replacement a day ahead, so it is propagated when needed
PROPAGATION_S = 180                   # Metered: a new credential can take up to ~2 minutes to work
_mint_lock = asyncio.Lock()


def _relay_servers(username: str, password: str) -> list[dict]:
    return [
        {
            "urls": [
                "turn:global.relay.metered.ca:80",
                "turn:global.relay.metered.ca:80?transport=tcp",
                "turn:global.relay.metered.ca:443",
                "turns:global.relay.metered.ca:443?transport=tcp",
            ],
            "username": username,
            "credential": password,
        }
    ]


async def _mint() -> dict:
    # METERED_API_KEY is the workspace SECRET key: it mints TURN credentials server-side, so
    # the secret never reaches the phones.
    async with httpx.AsyncClient(timeout=8.0) as client:
        r = await client.post(
            f"https://{settings.metered_app_name}.metered.live/api/v1/turn/credential",
            params={"secretKey": settings.metered_api_key},
            json={"expiryInSeconds": CREDENTIAL_TTL_S, "label": "sensocrypt-call"},
        )
        r.raise_for_status()
        return r.json()


def _row_id() -> int:
    """One stored credential per Metered account. Keyed by the app name so that switching to a
    different Metered account (new METERED_APP_NAME / key) starts from a fresh credential instead of
    serving the old account's login from the database."""
    return zlib.crc32(settings.metered_app_name.encode()) % 2_000_000_000 + 1


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def ensure_credential() -> tuple[str, str] | None:
    """The (username, password) to hand out now, minting/renewing ahead of need.

    Serves the current credential until it is nearly expired; a replacement is minted a day
    early and only promoted once it has had PROPAGATION_S to start working at the relay."""
    now = datetime.now(timezone.utc)
    async with _mint_lock:
        async with async_session() as db:
            rid = _row_id()
            row = await db.get(TurnCredential, rid)
            if row is None:
                row = TurnCredential(id=rid)
                db.add(row)
            exp, nexp = _aware(row.expires_at), _aware(row.next_expires_at)
            ncreated = _aware(row.next_created_at)

            # promote a matured replacement once the current one is (nearly) gone
            if row.next_username and ncreated and (now - ncreated).total_seconds() >= PROPAGATION_S \
                    and (exp is None or (exp - now).total_seconds() < 300):
                row.username, row.password, row.expires_at, row.created_at = (
                    row.next_username, row.next_password, nexp, ncreated)
                row.next_username = row.next_password = row.next_expires_at = row.next_created_at = None
                exp, nexp = nexp, None

            usable = row.username is not None and exp is not None and exp > now   # still accepted until it really expires
            need_next = (not row.next_username) and (
                not usable or (exp - now).total_seconds() < RENEW_BEFORE_EXPIRY_S)
            if need_next:
                try:
                    cred = await _mint()
                except httpx.HTTPStatusError as e:
                    # Deliberately not logging the exception text: it contains the URL (and key).
                    logger.error("Metered rejected the TURN credential request: HTTP %s body=%r",
                                 e.response.status_code, e.response.text[:300])
                    cred = None
                except Exception as e:  # noqa: BLE001
                    logger.error("could not fetch TURN credentials (%s)", type(e).__name__)
                    cred = None
                if cred is not None:
                    if not usable:   # nothing to serve yet: this one becomes current immediately
                        row.username, row.password = cred["username"], cred["password"]
                        row.expires_at, row.created_at = now + timedelta(seconds=CREDENTIAL_TTL_S), now
                        usable = True
                        logger.warning("TURN credential minted just now: the relay may take ~2 min to accept it")
                    else:
                        row.next_username, row.next_password = cred["username"], cred["password"]
                        row.next_expires_at, row.next_created_at = now + timedelta(seconds=CREDENTIAL_TTL_S), now
                        logger.info("TURN replacement credential minted ahead of expiry")
            await db.commit()
            return (row.username, row.password) if usable else None


@router.get("/ice-servers")
async def get_ice_servers(_: uuid.UUID = Depends(get_current_user_id)) -> dict:
    if not (settings.metered_app_name and settings.metered_api_key):
        logger.warning(
            "TURN not configured: METERED_APP_NAME set=%s, METERED_API_KEY set=%s; serving STUN only",
            bool(settings.metered_app_name), bool(settings.metered_api_key),
        )
        return {"ice_servers": STUN_ONLY, "turn": False}
    try:
        cred = await ensure_credential()
    except Exception as e:  # noqa: BLE001 -- never fail a call over the relay lookup
        logger.error("TURN credential lookup failed (%s); falling back to STUN only", type(e).__name__)
        cred = None
    if cred is None:
        return {"ice_servers": STUN_ONLY, "turn": False}
    return {"ice_servers": STUN_ONLY + _relay_servers(*cred), "turn": True}
