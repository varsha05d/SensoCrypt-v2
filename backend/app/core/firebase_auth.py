"""Server-side verification of Firebase phone-auth ID tokens.

The client authenticates the phone number directly with Firebase (OTP send/verify happens
entirely between the Android app and Firebase -- we never see the OTP itself, never send an
SMS ourselves, and never pay for one). What we verify here is the resulting Firebase ID
token, which Firebase cryptographically signs and which carries the verified phone number as
a claim. This is the boundary between "Firebase says this phone number is real" and "our own
User record for that phone number."
"""

import json

import firebase_admin
from firebase_admin import auth as firebase_auth_sdk
from firebase_admin import credentials

from app.core.config import settings

_app: firebase_admin.App | None = None


def _get_app() -> firebase_admin.App:
    global _app
    if _app is None:
        if not settings.firebase_service_account_json:
            raise RuntimeError(
                "FIREBASE_SERVICE_ACCOUNT_JSON is not set -- phone auth can't verify tokens "
                "without it. Paste the full service-account JSON (Firebase Console -> "
                "Project Settings -> Service Accounts -> Generate new private key) as this "
                "env var's value."
            )
        cred = credentials.Certificate(json.loads(settings.firebase_service_account_json))
        _app = firebase_admin.initialize_app(cred)
    return _app


class PhoneAuthError(Exception):
    pass


def verify_phone_id_token(id_token: str) -> str:
    """Verifies a Firebase ID token and returns the verified phone number (E.164, e.g.
    "+919876543210") it carries. Raises PhoneAuthError on any invalid/expired/malformed
    token, or if the token doesn't actually carry a verified phone number claim (e.g. it's
    an email/anonymous-auth token from a misconfigured client)."""
    try:
        decoded = firebase_auth_sdk.verify_id_token(id_token, app=_get_app())
    except Exception as exc:  # noqa: BLE001 -- firebase_admin raises several distinct types
        raise PhoneAuthError(f"invalid Firebase ID token: {exc}") from exc

    phone_number = decoded.get("phone_number")
    if not phone_number:
        raise PhoneAuthError("token does not carry a verified phone number")
    return phone_number
