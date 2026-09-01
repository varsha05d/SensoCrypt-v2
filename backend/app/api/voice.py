"""AI-vs-human voice detection (in-progress feature, voice-detection branch only -- not
wired into the main app flow yet).

The model itself does NOT run in this backend -- RawNet2 + torch measure ~671MB RSS at
runtime, more than this backend's entire 512MB Render free-tier budget. Instead, this is
a thin proxy: it forwards the uploaded audio to a separate Google Cloud Run service
(see voice-service/ at the repo root, deployed independently via `gcloud run deploy`,
not via this repo's own git history) that does the actual inference, and relays its
verdict back.

This endpoint exists for standalone testing (curl / a proof-of-concept script) while the
real Android audio-capture pipeline and the "check periodically during a call" design are
still being built. Nothing on the `main` branch calls this.
"""

import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, UploadFile

from app.api.deps import get_current_user_id
from app.core.config import settings

router = APIRouter(prefix="/api/v1/voice", tags=["voice-detection"])


@router.post("/detect")
async def detect_voice(
    file: UploadFile,
    user_id: uuid.UUID = Depends(get_current_user_id),
) -> dict:
    if not settings.voice_service_url or not settings.voice_service_api_key:
        raise HTTPException(status_code=503, detail="voice detection service not configured")

    wav_bytes = await file.read()

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{settings.voice_service_url.rstrip('/')}/detect",
                headers={"X-API-Key": settings.voice_service_api_key},
                files={"file": (file.filename or "audio.wav", wav_bytes, "audio/wav")},
            )
    except httpx.RequestError as exc:
        # Cloud Run scale-to-zero means the first request after idle can be slow to wake
        # the container (or, rarely, fail outright) -- surface as a clean 503 rather than
        # an unhandled connection error.
        raise HTTPException(status_code=503, detail=f"voice detection service unreachable: {exc}") from exc

    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    return resp.json()
