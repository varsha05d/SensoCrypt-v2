"""Server-side OTP send/verify via MSG91's OTP API.

Unlike Firebase Phone Auth, the OTP itself is generated and checked by MSG91 on our
backend's behalf -- the Android app never talks to MSG91 directly; it only talks to our
own /auth/phone/send-otp and /auth/phone/{signup,login} endpoints, which proxy to MSG91.
Sending through our backend (rather than a client-side SDK) also means the authkey never
ships inside the APK.
"""

import httpx

from app.core.config import settings

_BASE_URL = "https://control.msg91.com/api/v5/otp"


class PhoneAuthError(Exception):
    pass


def _mobile_for_msg91(phone_number: str) -> str:
    # MSG91 expects the number without a leading "+" (e.g. "919876543210").
    return phone_number.lstrip("+")


def _require_auth_key() -> str:
    if not settings.msg91_auth_key:
        raise RuntimeError(
            "MSG91_AUTH_KEY is not set -- phone auth can't send or verify OTPs without it."
        )
    return settings.msg91_auth_key


async def send_otp(phone_number: str) -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            _BASE_URL,
            params={"authkey": _require_auth_key(), "mobile": _mobile_for_msg91(phone_number)},
        )
    data = resp.json()
    if data.get("type") != "success":
        raise PhoneAuthError(f"MSG91 failed to send OTP: {data.get('message', data)}")


async def verify_otp(phone_number: str, otp: str) -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{_BASE_URL}/verify",
            params={
                "authkey": _require_auth_key(),
                "mobile": _mobile_for_msg91(phone_number),
                "otp": otp,
            },
        )
    data = resp.json()
    if data.get("type") != "success":
        raise PhoneAuthError("incorrect or expired code")
