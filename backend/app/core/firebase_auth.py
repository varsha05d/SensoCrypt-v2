"""Lazy Firebase Admin SDK init, used only for Cloud Messaging (incoming-call push) --
phone-number OTP auth goes through MSG91 instead (see app/core/msg91_auth.py)."""

import json

import firebase_admin
from firebase_admin import credentials

from app.core.config import settings

_app: firebase_admin.App | None = None


def _get_app() -> firebase_admin.App:
    global _app
    if _app is None:
        if not settings.firebase_service_account_json:
            raise RuntimeError(
                "FIREBASE_SERVICE_ACCOUNT_JSON is not set -- FCM push can't be sent without "
                "it. Paste the full service-account JSON (Firebase Console -> Project "
                "Settings -> Service Accounts -> Generate new private key) as this env var's "
                "value."
            )
        cred = credentials.Certificate(json.loads(settings.firebase_service_account_json))
        _app = firebase_admin.initialize_app(cred)
    return _app
