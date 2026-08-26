"""Phone-number signup/login (v2): the Android app authenticates the phone number with
Firebase directly (OTP send + verify happens client<->Firebase, we never see the OTP and
never send an SMS ourselves), then hands us the resulting Firebase ID token. We verify that
token server-side and either create or look up a User for the verified phone number.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import settings
from app.core.firebase_auth import PhoneAuthError, verify_phone_id_token
from app.db.models import User
from app.db.session import get_db
from app.schemas import PhoneAuthResponse, PhoneLoginRequest, PhoneSignupRequest

router = APIRouter(prefix="/api/v1/auth/phone", tags=["auth-phone"])


@router.post("/signup", response_model=PhoneAuthResponse)
async def phone_signup(body: PhoneSignupRequest, db: AsyncSession = Depends(get_db)) -> PhoneAuthResponse:
    try:
        phone_number = verify_phone_id_token(body.firebase_id_token)
    except PhoneAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    existing = await db.scalar(select(User).where(User.phone_number == phone_number))
    if existing is not None:
        raise HTTPException(status_code=409, detail="an account already exists for this phone number -- log in instead")

    user = User(phone_number=phone_number, name=body.name, email=body.email)
    db.add(user)
    await db.commit()

    token = crypto.issue_user_token(str(user.user_id), ttl_s=settings.user_session_token_ttl_s)
    return PhoneAuthResponse(user_id=str(user.user_id), token=token, expires_in=settings.user_session_token_ttl_s)


@router.post("/login", response_model=PhoneAuthResponse)
async def phone_login(body: PhoneLoginRequest, db: AsyncSession = Depends(get_db)) -> PhoneAuthResponse:
    try:
        phone_number = verify_phone_id_token(body.firebase_id_token)
    except PhoneAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    user = await db.scalar(select(User).where(User.phone_number == phone_number))
    if user is None:
        raise HTTPException(status_code=404, detail="no account for this phone number -- sign up first")

    token = crypto.issue_user_token(str(user.user_id), ttl_s=settings.user_session_token_ttl_s)
    return PhoneAuthResponse(user_id=str(user.user_id), token=token, expires_in=settings.user_session_token_ttl_s)
