"""Shared FastAPI dependencies for v2's user-auth endpoints."""

import uuid

from fastapi import Header, HTTPException

from app.core import crypto


async def get_current_user_id(authorization: str = Header(...)) -> uuid.UUID:
    """Expects `Authorization: Bearer <paseto user token>`, as issued by
    /api/v1/auth/phone/signup or /login. Raises 401 on anything invalid/expired/missing."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing or malformed Authorization header")
    token = authorization.removeprefix("Bearer ")
    try:
        payload = crypto.verify_user_token(token)
    except Exception as exc:  # noqa: BLE001 -- pyseto/json raise several distinct types on bad input
        raise HTTPException(status_code=401, detail="invalid or expired session token") from exc
    return uuid.UUID(payload["user_id"])
