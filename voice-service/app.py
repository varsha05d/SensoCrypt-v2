"""Standalone AI-vs-human voice detection service, deployed as its own Hugging Face
Space -- deliberately NOT part of the main SensoCrypt v2 backend (see detector.py's
docstring: RawNet2 alone uses ~670MB RAM at runtime, more than Render's entire 512MB
free-tier budget, so it can't run alongside the rest of the app there).

Gated by a shared-secret header (set as a Space secret, `VOICE_SERVICE_API_KEY`) rather
than left open, since this is a public URL and inference isn't free to run indefinitely
for strangers.
"""

import os

from fastapi import FastAPI, Header, HTTPException, UploadFile

import detector

app = FastAPI(title="SensoCrypt Voice Detection Service")


def _require_api_key(x_api_key: str | None) -> None:
    expected = os.environ.get("VOICE_SERVICE_API_KEY")
    if not expected:
        raise HTTPException(status_code=500, detail="VOICE_SERVICE_API_KEY not configured on this Space")
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/detect")
async def detect(file: UploadFile, x_api_key: str | None = Header(default=None)) -> dict:
    _require_api_key(x_api_key)
    wav_bytes = await file.read()
    try:
        return detector.detect_from_wav_bytes(wav_bytes)
    except detector.VoiceDetectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
