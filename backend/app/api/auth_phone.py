"""Phone-number signup/login (v2): OTP send/entry/verify happens entirely on-device via
MSG91's Kotlin SDK against their default (non-DLT) template -- the Android app talks to
this backend only after it already has a verified access token from MSG91. We verify that
token server-side (see app/core/msg91_auth.py), then create or look up a User for the
phone number it carries.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import settings
from app.core.msg91_auth import PhoneAuthError, verify_widget_access_token
from app.db.models import User
from app.db.session import get_db
from app.schemas import PhoneAuthResponse, PhoneLoginRequest, PhoneSignupRequest

router = APIRouter(prefix="/api/v1/auth/phone", tags=["auth-phone"])


@router.post("/signup", response_model=PhoneAuthResponse)
async def phone_signup(body: PhoneSignupRequest, db: AsyncSession = Depends(get_db)) -> PhoneAuthResponse:
    try:
        phone_number = await verify_widget_access_token(body.access_token)
    except PhoneAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    existing = await db.scalar(select(User).where(User.phone_number == phone_number))
    if existing is not None:
        raise HTTPException(status_code=409, detail="an account already exists for this phone number -- log in instead")

    user = User(phone_number=phone_number, name=body.name, email=body.email)
    db.add(user)
    await db.commit()

    token = crypto.issue_user_token(str(user.user_id))
    return PhoneAuthResponse(user_id=str(user.user_id), token=token, expires_in=settings.session_token_ttl_s)


@router.post("/login", response_model=PhoneAuthResponse)
async def phone_login(body: PhoneLoginRequest, db: AsyncSession = Depends(get_db)) -> PhoneAuthResponse:
    try:
        phone_number = await verify_widget_access_token(body.access_token)
    except PhoneAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    user = await db.scalar(select(User).where(User.phone_number == phone_number))
    if user is None:
        raise HTTPException(status_code=404, detail="no account for this phone number -- sign up first")

    token = crypto.issue_user_token(str(user.user_id))
    return PhoneAuthResponse(user_id=str(user.user_id), token=token, expires_in=settings.session_token_ttl_s)
