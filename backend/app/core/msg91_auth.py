"""Server-side verification of MSG91 OTP Widget access tokens.

OTP send + entry + verify happen entirely on-device via MSG91's Kotlin SDK
(com.msg91.lib:sendotp), which uses MSG91's own default (non-DLT-registered) OTP
template -- this backend never sends an OTP itself. What the app hands us afterward is
the resulting access token (a JWT), which we verify server-side via MSG91's
verifyAccessToken API before trusting the phone number it carries.
"""

import base64
import json

import httpx

from app.core.config import settings

_VERIFY_URL = "https://control.msg91.com/api/v5/widget/verifyAccessToken"


class PhoneAuthError(Exception):
    pass


def _require_auth_key() -> str:
    if not settings.msg91_auth_key:
        raise RuntimeError("MSG91_AUTH_KEY is not set -- phone auth can't verify tokens without it.")
    return settings.msg91_auth_key


def _decode_jwt_payload(token: str) -> dict:
    try:
        payload_b64 = token.split(".")[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(padded))
    except Exception as exc:  # noqa: BLE001 -- malformed/foreign token, all treated the same
        raise PhoneAuthError(f"couldn't decode access token payload: {exc}") from exc


async def verify_widget_access_token(access_token: str) -> str:
    """Verifies a widget access token server-side (confirms MSG91 actually issued it,
    rather than trusting an unverified client-decoded JWT) and returns the E.164 phone
    number (leading '+') it was issued for."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            _VERIFY_URL,
            headers={"Content-Type": "application/json"},
            json={"authkey": _require_auth_key(), "access-token": access_token},
        )
    data = resp.json()
    if data.get("type") == "error":
        raise PhoneAuthError(f"invalid or expired access token: {data.get('message', data)}")

    claims = _decode_jwt_payload(access_token)
    identifier = claims.get("identifier") or claims.get("mobile") or claims.get("phone")
    if not identifier:
        raise PhoneAuthError("access token does not carry a verified identifier")
    identifier = str(identifier)
    return identifier if identifier.startswith("+") else f"+{identifier}"
