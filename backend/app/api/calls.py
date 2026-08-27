"""Phone-number calling (v2): place a call by phone number (pushes an incoming-call
notification to the callee via FCM), and list past calls (call logs, with failed-
verification attempts flagged for the client's fraud warning).

Deliberately does NOT include the pre-connect verification gate or session-key handoff yet
-- those live in a dedicated WS module once the liveness-capture port is done (see
app/api/call_verify.py, next). This module only covers: resolving a phone number to a user,
creating the Call row, and triggering the push.
"""

import base64
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from firebase_admin import messaging
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_id
from app.core import call_coordinator, crypto, nonce as nonce_store
from app.core.firebase_auth import _get_app
from app.db.models import Call, User
from app.db.session import get_db
from app.schemas import (
    CallLogEntry,
    CallStatusResponse,
    FcmTokenRequest,
    PlaceCallRequest,
    PlaceCallResponse,
    SessionKeyRequest,
    SessionKeyResponse,
    UserProfileResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["calls"])


@router.get("/users/me", response_model=UserProfileResponse)
async def get_my_profile(
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> UserProfileResponse:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="unknown user")
    return UserProfileResponse(
        user_id=str(user.user_id), name=user.name, email=user.email, phone_number=user.phone_number
    )


@router.post("/users/me/fcm-token")
async def set_fcm_token(
    body: FcmTokenRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> dict:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="unknown user")
    user.fcm_token = body.fcm_token
    await db.commit()
    return {"ok": True}


@router.post("/calls", response_model=PlaceCallResponse)
async def place_call(
    body: PlaceCallRequest,
    caller_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> PlaceCallResponse:
    caller = await db.get(User, caller_id)
    callee = await db.scalar(select(User).where(User.phone_number == body.callee_phone_number))
    if callee is None:
        raise HTTPException(status_code=404, detail="no SensoCrypt account for that phone number")
    if callee.user_id == caller_id:
        raise HTTPException(status_code=400, detail="can't call yourself")

    call = Call(caller_user_id=caller_id, callee_user_id=callee.user_id, state="RINGING")
    db.add(call)
    await db.commit()

    if callee.fcm_token:
        _get_app()  # ensures firebase_admin is initialized before messaging.send()
        try:
            message_id = messaging.send(
                messaging.Message(
                    token=callee.fcm_token,
                    data={"type": "incoming_call", "call_id": str(call.call_id), "caller_name": caller.name},
                    android=messaging.AndroidConfig(priority="high"),
                )
            )
            logger.info("call %s: FCM push sent to callee %s (message_id=%s)", call.call_id, callee.user_id, message_id)
        except Exception:
            # A bad/expired token (uninstalled app, token rotated) shouldn't fail placing the
            # call -- it just means this call rings nowhere, same as having no token on file.
            # Logged so a real delivery problem is diagnosable instead of silently invisible.
            logger.exception("call %s: FCM push to callee %s failed", call.call_id, callee.user_id)
    else:
        logger.info("call %s: callee %s has no fcm_token on file, nothing to push", call.call_id, callee.user_id)
    # No fcm_token on file for the callee: the call row still exists (shows as a missed/
    # unreachable call in logs) but nothing rings on their end. Not raising an error here --
    # a caller shouldn't be able to tell whether a number has the app installed or not.

    return PlaceCallResponse(call_id=str(call.call_id))


@router.post("/calls/{call_id}/accept")
async def accept_call(
    call_id: str,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Callee accepts the incoming-call push -- starts the shared 30s pre-connect
    verification window (app/core/call_coordinator.start_window). Both sides then run their
    own liveness capture against /ws/telemetry/{session_id}?call_id=...&side=..., same
    mechanism as the original SensoCrypt's in-call checking, just moved earlier."""
    try:
        call_uuid = uuid.UUID(call_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="unknown call") from exc
    call = await db.get(Call, call_uuid)
    if call is None or call.callee_user_id != user_id:
        raise HTTPException(status_code=404, detail="unknown call")
    if call.state != "RINGING":
        raise HTTPException(status_code=409, detail=f"call is not ringing (state={call.state})")

    call.state = "VERIFYING"
    await db.commit()
    call_coordinator.start_window(call_id)
    return {"ok": True, "verify_window_s": call_coordinator.VERIFY_WINDOW_S}


@router.get("/calls/{call_id}/status", response_model=CallStatusResponse)
async def get_call_status(
    call_id: str,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> CallStatusResponse:
    """Polled by the caller while on the ringing screen -- there's no push notification
    for "the callee accepted", so this is how the caller learns to move on to its own
    liveness capture (state flips RINGING -> VERIFYING) or that the call was declined/
    ended without ever being answered."""
    try:
        call_uuid = uuid.UUID(call_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="unknown call") from exc
    call = await db.get(Call, call_uuid)
    if call is None or user_id not in (call.caller_user_id, call.callee_user_id):
        raise HTTPException(status_code=404, detail="unknown call")
    return CallStatusResponse(state=call.state)


@router.post("/calls/{call_id}/decline")
async def decline_call(
    call_id: str,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        call_uuid = uuid.UUID(call_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="unknown call") from exc
    call = await db.get(Call, call_uuid)
    if call is None or call.callee_user_id != user_id:
        raise HTTPException(status_code=404, detail="unknown call")
    if call.state != "RINGING":
        raise HTTPException(status_code=409, detail=f"call is not ringing (state={call.state})")
    call.state = "DECLINED"
    await db.commit()
    return {"ok": True}


@router.post("/calls/{call_id}/session-key", response_model=SessionKeyResponse)
async def get_call_session_key(call_id: str, body: SessionKeyRequest) -> SessionKeyResponse:
    """Called by each side once it believes it's verified, with the session_id from its own
    device auth+kex handshake (unchanged from the original flow). Returns this side's
    wrapped copy of the ONE shared call session key the backend generated -- only once
    call_coordinator confirms BOTH sides actually reached TRUSTED. k_chal is single-use
    (nonce store), so a repeat call with the same session_id after the first successful
    fetch correctly gets nothing."""
    session_key = call_coordinator.get_or_create_session_key(call_id)
    if session_key is None:
        return SessionKeyResponse(wrapped_key_b64=None)

    k_chal_hex = await nonce_store.consume(f"kchal:{body.session_id}")
    if k_chal_hex is None:
        raise HTTPException(status_code=401, detail="no key-exchange material for that session_id (run /session/kex first)")

    wrapped = crypto.wrap_call_session_key(session_key, bytes.fromhex(k_chal_hex), call_id)
    return SessionKeyResponse(wrapped_key_b64=base64.b64encode(wrapped).decode())


@router.get("/calls/logs", response_model=list[CallLogEntry])
async def call_logs(
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> list[CallLogEntry]:
    rows = (
        await db.execute(
            select(Call)
            .where(or_(Call.caller_user_id == user_id, Call.callee_user_id == user_id))
            .order_by(Call.created_at.desc())
        )
    ).scalars().all()

    entries = []
    for call in rows:
        is_outgoing = call.caller_user_id == user_id
        other_id = call.callee_user_id if is_outgoing else call.caller_user_id
        other = await db.get(User, other_id)
        entries.append(
            CallLogEntry(
                call_id=str(call.call_id),
                other_party_name=other.name if other else "Unknown",
                other_party_phone=other.phone_number if other else "",
                direction="outgoing" if is_outgoing else "incoming",
                state=call.state,
                created_at=call.created_at.isoformat(),
            )
        )
    return entries
