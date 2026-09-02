# SensoCrypt v2

A phone-calling app where **before a call connects**, both people have to prove — using
their phone's own camera and motion sensors — that they're a real, live person on a real,
present device, not a pre-recorded video, a screen replay, or an injected/deepfaked video
feed. Only once both sides pass does the call actually connect, over an encrypted channel
with a unique key generated fresh for that one call.

This document explains what problem each part of the app solves and exactly which file
does it, so anyone picking this up can find their way around without re-deriving it from
the code.

## The problem, in plain terms

Anyone can screen-share a fake video, play back a recording of a real person, or run a
face-swap/deepfake feed into a video call. A basic "is there a face in frame" check can't
tell any of that apart from the real thing. SensoCrypt's answer is to check something a
recording or an injected feed can't fake in real time: **does the phone's own motion sensor
agree with what the camera sees, and does the scene react correctly to a random light
pattern chosen at that exact moment?**

Two independent checks run before every call connects:

- **Channel A — gyroscope vs. camera motion.** When you move the phone, the camera image
  moves too, in a way that's mathematically predictable from how much the phone actually
  rotated. The backend compares the phone's real gyroscope readings against the motion it
  measures directly from the video frames. A live camera's image motion and the gyroscope
  agree; a pre-recorded video playing on a screen (or an injected feed with no gyroscope
  behind it at all) can't produce image motion that correlates with *this* phone's *actual*
  real-time rotation.
- **Channel B — random light challenge.** The screen flashes a short, randomly-generated
  sequence of colors, chosen fresh for that moment — never the same sequence twice, so it
  can't be pre-recorded or guessed in advance. The backend checks whether the brightness the
  camera picks up actually tracks that sequence. A static photo or a screen replay won't
  react to it; a live camera pointed at a real, lit scene will.

Either channel passing is enough for that instant (they're independent, physically
different attacks each one rules out) — but the system requires **several consecutive
good readings in a row**, not just one lucky moment, before it calls someone verified. That
sustained-evidence requirement is what actually makes this hard to fake, not any single
check in isolation.

Once both people on a call pass, the backend generates a **brand-new random encryption
key just for that call**, hands a copy to each side over their own already-secure channel,
and throws it away the instant the call ends. If verification fails or times out (30
seconds), the call **never connects at all** — it's logged as a likely fraud attempt
instead, visible in Call Logs.

## How a call actually goes, start to finish

1. **Sign up / log in** by phone number (real SMS OTP via Firebase, not a code we send
   ourselves).
2. **Dial** a phone number. The backend looks up who that number belongs to and sends them
   a push notification — their phone rings even if the app isn't open.
3. They **accept**. Both phones now independently start their own ~30-second camera +
   gyroscope capture — this is the verification step, and it happens **before** any call
   audio/video exists yet, not alongside one.
4. Each phone streams what it sees/feels to the backend in real time. The backend scores it
   continuously (Channel A + Channel B, above) and tells each side whether it's currently
   trusted.
5. The moment **both** sides have been continuously trusted for long enough, the backend:
   generates the call's one-time encryption key, hands each side its copy, and unlocks the
   actual call signaling.
6. **The call connects** (video + audio, peer-to-peer where possible). Neither side could
   have gotten here without passing their own check.
7. If either side never reaches "trusted" within 30 seconds — camera covered, holding
   dead still, screen replay, whatever — the call is cut before it ever connects, and gets
   logged as **Likely Fraud** instead of a normal call.

## Where everything lives

### Backend (`backend/`) — FastAPI, Python

**The verification math itself — `backend/app/liveness/`:**

| File | What it does |
|---|---|
| `egomotion.py` | Turns two consecutive camera frames into "how fast did the camera actually rotate" (an angular velocity), by tracking how background features moved between them. Deliberately ignores the face/foreground — motion evidence has to come from the background, since a face is exactly what an attacker could be forging. |
| `imu.py` | Prepares the phone's raw gyroscope readings for comparison: resamples them onto the same time grid as the camera-derived motion, and band-pass filters both to the 0.4–8 Hz range real hand/head movement lives in (removing sensor drift and jitter noise on either side). |
| `align.py` | Finds the best time-lag and axis-mapping between the two signals (camera coordinate axes and phone sensor axes aren't the same, and network jitter means neither is the timestamp), then locks that alignment in for the rest of the session once it's confident. |
| `score.py` | Given the two aligned, filtered signals, computes how well they actually correlate — this correlation strength is Channel A's score, `S_A`. |
| `challenge.py` | Generates the random color-flash sequence for Channel B, and scores how well the camera's observed brightness matches that sequence afterward — this is `S_illum`. |
| `fusion.py` | Combines `S_A` and `S_illum` into one trust value each tick (takes whichever channel currently has the stronger evidence, since they're independent proofs, not a blend that could be dragged down by one noisy channel), and runs the **trust state machine** — `DEGRADED → SUSPECT/TRUSTED`, requiring several consecutive good readings, not one, before granting `TRUSTED`. |
| `worker.py` | Ties all of the above together: one instance runs per verification session, fed a stream of camera+gyro chunks, re-scoring on every new chunk and tracking the illumination challenge's timing. |
| `constants.py` | The actual thresholds and timing constants everything above uses (how many consecutive good readings are required, the trust probability cutoff, the motion filter band, etc). |

**Calling + accounts + crypto — `backend/app/`:**

| File | What it does |
|---|---|
| `api/auth_phone.py` | Phone-number signup/login: verifies the Firebase ID token the app hands it, creates or looks up the `User`. |
| `api/calls.py` | Placing a call (looks up the callee by phone number, sends them a push notification), accepting/declining, polling call status, and Call Logs. |
| `core/call_coordinator.py` | The 30-second verification window itself: tracks which side(s) of a specific call have reached `TRUSTED`, generates that call's one-time session key the moment both sides do, and fails the call out if the window expires first. This is what actually gates whether a call is allowed to connect at all. |
| `api/telemetry.py` (WebSocket) | Where each phone streams its live camera+gyro chunks during verification — feeds straight into `liveness/worker.py` per session. |
| `api/signal.py` (WebSocket) | The actual call's offer/answer/network-address exchange (WebRTC signaling) — but only relays anything once `call_coordinator` says that call reached `TRUSTED` on both sides. |
| `core/crypto.py` | The cryptographic building blocks: per-session key derivation, and issuing/verifying the signed tokens used for both device sessions and logged-in users. |
| `core/attestation.py` | Verifies a device's hardware attestation (Android Keystore) at enrollment time — confirms the app is running on genuine, unmodified Android hardware, signed with the expected release key, not a rooted/repackaged copy. |
| `db/models.py` | The database tables: `User`, `Device`, `Call`. |

### Android (`android/app/src/main/java/com/sensocrypt/`)

| File | What it does |
|---|---|
| `call/VerifyScreen.kt` | The pre-connect verification screen: opens the camera, starts streaming frames + gyroscope readings to the backend, runs the light-flash challenge on screen when told to, and shows the live trust state. This is Channel A and B's client side. |
| `capture/LiveStreamer.kt` | Registers for `Sensor.TYPE_GYROSCOPE` and buffers gyroscope readings alongside captured camera frames into the chunks sent to the backend. |
| `capture/FrameCapture.kt`, `JpegEncoder.kt`, `Synchronizer.kt` | Capturing and encoding camera frames, and keeping frame/gyro timestamps aligned before they're sent. |
| `challenge/ChallengeFlash.kt` | Actually flips the screen through the random color sequence the backend told it to, at the exact moment the backend expects (Channel B). |
| `call/DialerScreen.kt`, `IncomingCallScreen.kt` | Placing a call by phone number; showing/accepting an incoming call. |
| `call/ConnectedCallScreen.kt`, `WebRtcSession.kt` | The actual connected call itself (video/audio) — only ever reached after `VerifyScreen` succeeds. |
| `call/CallLogsScreen.kt` | Past calls, color-coded: green "Verified" for calls that connected, red "Likely Fraud" for ones that failed/timed-out verification. |
| `crypto/KeystoreManager.kt` | Generates and uses the device's hardware-backed (Android Keystore) signing key — the client side of `backend/app/core/attestation.py`'s check. |
| `crypto/SessionCrypto.kt`, `CallSessionCrypto.kt`, `TelemetryCrypto.kt` | Deriving this device's own secure channel with the backend, unwrapping the one-time call key the backend hands over once verified, and encrypting the telemetry chunks sent during verification. |
| `identity/UserSession.kt`, `IdentityStore.kt` | Persisted login session (phone-auth token) vs. persisted device identity (hardware attestation) — two separate things: a device can be enrolled before anyone's logged in. |
| `net/` | Thin HTTP/WebSocket clients for each backend endpoint above (one file per API area — `CallsApi.kt`, `AuthPhoneApi.kt`, `TelemetrySocket.kt`, `SignalSocket.kt`, etc). |

## Running it yourself

```bash
docker compose up -d
```
See `backend/.env` (gitignored, not committed) for the required environment variables —
each one is documented in `backend/app/core/config.py`.

## Deployment

`render.yaml` at the repo root defines this app's Render Blueprint (web service + a free
Postgres database). The Android app's `net/BackendConfig.kt` points at that deployed URL.

## A related, separate feature in progress

There's an in-progress `voice-detection` branch adding **AI-vs-human voice detection**
during a call (is the *voice* on the other end synthetic, separate from the *device*
liveness checks above). Deliberately kept off this branch until it's further along — see
that branch's own `VOICE_DETECTION.md` for what it does and its current known limitations.
