# SensoCrypt v2

Phone-number calling with a pre-connect liveness verification gate, built on top of the
original [SensoCrypt](https://github.com/varsha05d/SensoCrypt) project's hardware-attestation
and liveness-detection core.

## What's different from v1

- Real accounts: signup/login by phone number, OTP-verified via Firebase Phone Auth (no
  per-SMS cost).
- Calling by phone number instead of sharing a one-time code, with push-notification
  ringing (Firebase Cloud Messaging).
- Liveness verification happens **before** a call connects, not alongside an already-live
  one: both parties get up to 30 seconds to verify; the call only connects once both have.
  A failed/timed-out verification never connects the call at all -- it's logged instead,
  flagged as likely fraud.
- A unique, backend-generated session key per call, delivered to both verified parties over
  their already-encrypted individual channels, and discarded the moment the call ends.

## Structure

- `backend/` -- FastAPI service. `app/api/auth.py` + `app/api/session.py` are the original
  device-attestation/crypto flow, unchanged. `app/api/auth_phone.py`, `app/api/calls.py`,
  and `app/core/call_coordinator.py` are new for v2.
- `android/` -- not yet built for v2 (in progress).

## Running locally

```bash
docker compose up -d
```

See `backend/.env.example` for required environment variables.

## Deployment

`render.yaml` at the repo root defines a Render Blueprint (web service + free Postgres).
