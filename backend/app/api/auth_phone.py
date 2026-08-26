"""Phone-number signup/login (v2): OTP is sent and verified via MSG91 (see
app/core/msg91_auth.py) -- the Android app talks only to our own backend, never to MSG91
directly. We verify the code server-side, then create or look up a User for that phone
number.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import settings
from app.core.msg91_auth import PhoneAuthError, send_otp, verify_otp
from app.db.models import User
from app.db.session import get_db
from app.schemas import (
    OkResponse,
    PhoneAuthResponse,
    PhoneLoginRequest,
    PhoneSignupRequest,
    SendOtpRequest,
)

router = APIRouter(prefix="/api/v1/auth/phone", tags=["auth-phone"])


@router.post("/send-otp", response_model=OkResponse)
async def phone_send_otp(body: SendOtpRequest) -> OkResponse:
    try:
        await send_otp(body.phone_number)
    except PhoneAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return OkResponse(ok=True)


@router.post("/signup", response_model=PhoneAuthResponse)
async def phone_signup(body: PhoneSignupRequest, db: AsyncSession = Depends(get_db)) -> PhoneAuthResponse:
    try:
        await verify_otp(body.phone_number, body.otp)
    except PhoneAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    existing = await db.scalar(select(User).where(User.phone_number == body.phone_number))
    if existing is not None:
        raise HTTPException(status_code=409, detail="an account already exists for this phone number -- log in instead")

    user = User(phone_number=body.phone_number, name=body.name, email=body.email)
    db.add(user)
    await db.commit()

    token = crypto.issue_user_token(str(user.user_id))
    return PhoneAuthResponse(user_id=str(user.user_id), token=token, expires_in=settings.session_token_ttl_s)


@router.post("/login", response_model=PhoneAuthResponse)
async def phone_login(body: PhoneLoginRequest, db: AsyncSession = Depends(get_db)) -> PhoneAuthResponse:
    try:
        await verify_otp(body.phone_number, body.otp)
    except PhoneAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    user = await db.scalar(select(User).where(User.phone_number == body.phone_number))
    if user is None:
        raise HTTPException(status_code=404, detail="no account for this phone number -- sign up first")

    token = crypto.issue_user_token(str(user.user_id))
    return PhoneAuthResponse(user_id=str(user.user_id), token=token, expires_in=settings.session_token_ttl_s)
