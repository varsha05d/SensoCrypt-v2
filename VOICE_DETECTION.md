# AI-vs-human voice detection (voice-detection branch)

## What this solves

During a call, detect whether the other party's voice is AI-generated (a cloned/synthetic
voice used for a fraud attempt) or a real human -- continuously throughout the call, shown
as a live badge, not a one-time check. This is in addition to (not a replacement for) the
existing pre-connect liveness verification on `main`, which proves the device on the other
end is a real, present phone -- this feature instead looks at the voice itself.

This is still an in-progress feature on its own branch, deliberately kept off `main` so it
can't affect the already-tested, client-ready calling app. See "Known limitation" below
before treating it as done.

## Why it's three separate pieces, not one

The model (RawNet2 + torch) measures ~671MB RAM at runtime -- more than this backend's
entire 512MB Render free-tier budget, and more than an Android app should be shipping
inference for on-device. So inference runs as its own service, and everything else just
calls it:

```
Android (call in progress)
  -> captures the REMOTE party's decoded call audio (not the local mic)
  -> POST /api/v1/voice/detect  (this repo's backend, Bearer-authed)
       -> proxies to a separate Cloud Run service (not this repo's own deploy)
            -> runs RawNet2, returns {"label": "human"|"ai_generated", "confidence": 0-1}
       <- relays that verdict back
  <- live badge updates: "Analyzing voice..." / "Human voice" / "AI voice detected"
```

Repeats roughly every 4 seconds for the whole call, not just once.

## Where everything lives

### `voice-service/` -- the actual model, deployed to Google Cloud Run

Not deployed through this repo's git history at all -- it's `gcloud run deploy --source=.`
run directly from this directory. Nothing here is imported by the backend; the backend only
ever talks to it over HTTP.

- `app.py` -- FastAPI app: `GET /health` (no auth), `POST /detect` (multipart WAV upload,
  gated by an `X-API-Key` header checked against the `VOICE_SERVICE_API_KEY` env var set on
  the Cloud Run service).
- `detector.py` -- loads the RawNet2 checkpoint (`MattyB95/pre_trained_DF_RawNet2` from
  Hugging Face, lazily on first request, not at startup), decodes the uploaded WAV
  (stdlib `wave` + numpy, no extra audio-codec deps), resamples to 16kHz if needed, runs
  inference, returns the verdict. Full reasoning for why RawNet2 was picked over the other
  options tried (HuBERT, Wav2Vec2, WavLM, ViT) is in this file's own docstring, along with
  the one known upstream checkpoint quirk (a missing `Sinc_conv.filters` key -- deterministic,
  not learned, safe to skip) and why.
- `rawnet2_model.py`, `rawnet2_config.yaml` -- the model architecture itself and its config,
  vendored from the original paper's code (MIT-licensed, via the Jabberjay project).
- `Dockerfile` -- CPU-only torch (the default PyPI package is a multi-GB CUDA build this
  service has no use for), runs as a non-root user (a pattern borrowed from Hugging Face
  Spaces conventions, kept even after moving to Cloud Run).
- `README.md` -- the exact redeploy command, plus a note on Cloud Run's cold-image-pull
  startup-probe timing that testing this uncovered (the container itself starts in well
  under a second; the first pull of this image to a brand-new node can take longer than a
  short probe budget allows for).

### `backend/app/api/voice.py` -- thin proxy, this repo's normal deploy path

The only file in the backend that knows this feature exists.

- `POST /api/v1/voice/detect` -- auth-gated the same way every other v2 endpoint is
  (`get_current_user_id`), forwards the uploaded WAV to `VOICE_SERVICE_URL` with
  `VOICE_SERVICE_API_KEY` (both in `app/core/config.py`), relays the verdict back. Returns
  503 if unconfigured or if the Cloud Run service is unreachable (e.g. mid cold-start
  outside its own timeout budget).

No model code, no torch, nothing heavy lives in the backend itself -- deliberately, so this
feature can't push the backend over its host's memory limit.

### Android -- capture, upload, display

- `android/app/src/main/java/com/sensocrypt/call/VoiceDetectionRecorder.kt` -- implements
  WebRTC's `AudioTrackSink`, attached to the REMOTE party's decoded audio track (not the
  local mic -- WebRtcSession hands it the incoming `AudioTrack` via `onRemoteAudioTrack`).
  Buffers into ~4-second windows and wraps each as a 16-bit PCM WAV, matching whatever
  sample rate/channel count WebRTC actually delivers.
- `android/app/src/main/java/com/sensocrypt/net/VoiceApi.kt` -- `POST`s each window to the
  backend, Bearer-authed, matching the existing `UsersApi`/`CallsApi` pattern. A longer read
  timeout than the other API clients here -- the backend proxies to a scale-to-zero Cloud
  Run service, so a cold start can take the better part of a minute.
- `android/app/src/main/java/com/sensocrypt/call/WebRtcSession.kt` -- `onTrack` now handles
  the `AudioTrack` case (previously only handled `VideoTrack`) and surfaces it via the new
  `onRemoteAudioTrack` callback.
- `android/app/src/main/java/com/sensocrypt/call/ConnectedCallScreen.kt` -- wires the
  recorder in when the remote audio track arrives, launches a coroutine per finished window
  (never blocks WebRTC's own audio thread), guards against a slow request's result
  overwriting a newer one, and renders the live badge under the existing
  "Verified -- secure call" badge: gray "Analyzing voice..." until the first window
  resolves, green "Human voice" / red "AI voice detected" after. A failed window (network
  blip, cold start) is logged but doesn't interrupt the call or clear the last verdict.
- `android/app/src/main/java/com/sensocrypt/net/BackendConfig.kt` -- points at this branch's
  own Render service (see below), not the production one `main` uses.
- `android/app/src/main/res/xml/network_security_config.xml` -- only matters for local
  testing against a plain-http dev backend; has no effect once `BackendConfig.kt` points at
  a real `https://` host.

## Deployment

Two independent pieces, deployed two different ways:

- **Cloud Run service** (`voice-service/`): `gcloud run deploy --source=.` from that
  directory -- see its own `README.md` for the exact command. Scale-to-zero, so it costs
  nothing while idle.
- **Backend**: `render.yaml` at the repo root defines `sensocrypt-v2-backend-voicedetect` as
  its own Render web service + Postgres database, separate from main's
  `sensocrypt-v2-backend` / `sensocrypt-v2-db`. Deploy it as a **new** Render Blueprint
  instance pointed at the `voice-detection` branch -- not by re-running the production
  blueprint -- so it creates its own resources instead of touching the production ones.
  After creating it, set the `sync: false` env vars manually in the Render dashboard:
  `PASETO_LOCAL_KEY_HEX`, `PINNED_SIGNING_CERT_DIGESTS_HEX` (can stay empty for a debug-signed
  test build), `FIREBASE_SERVICE_ACCOUNT_JSON`, `VOICE_SERVICE_URL` (the Cloud Run service's
  URL), `VOICE_SERVICE_API_KEY` (matching what's set on the Cloud Run service).

## Known limitation (not yet solved)

RawNet2 was trained/validated on the ASVspoof2019-DF dataset -- clean, uncompressed studio
audio. Real call audio goes through Opus compression plus WebRTC's own noise
suppression/echo cancellation, which distorts the waveform in ways the model wasn't trained
to handle. Tested live on two real phones over an actual call: the same real human voice
flipped between "human" and "ai_generated" at 99%+ confidence across consecutive ~4-second
windows. The pipeline itself (capture, upload, proxy, inference, badge) works correctly
end-to-end -- the open problem is the model's accuracy specifically on compressed VoIP
audio, not the plumbing around it.
