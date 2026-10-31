# SensoCrypt v2 — Test-Case Evidence Guide

How to demonstrate each test case (TC01–TC09) live, with proof from **the app**, **the Render
logs** and **the automated test suite**.

## Before the demo

| Item | Detail |
|---|---|
| Backend | `https://sensocrypt-v2-backend.onrender.com/health` must return `{"status":"ok"}`. Free tier sleeps after 15 min idle — open this URL first and wait for it. |
| Phones | Two phones with the latest APK, both logged in. For a remote friend, both on the TURN-enabled build. |
| Render logs | Render → `sensocrypt-v2-backend` → **Logs**. Timestamps are **UTC** (IST = UTC + 5:30). Use the search box with the exact strings below. |
| Phone logs (optional) | Phone plugged in: `adb logcat \| grep -E "VerifyScreen\|ConnectedCall\|WebRtc ICE"` |
| Automated proof | `./scripts/run_tc_evidence.sh` — runs the tests and saves a dated report in `docs/evidence/`. |
| Screen recording | Record the phone screen and the Render Logs tab side by side for each case. |

Each verification line in the Render logs looks like:

```
verdict=ok r=0.71 S_A=0.45 … illum_verdict=ok S_illum=0.98 … state=TRUSTED channel_A=True channel_B=True camera_blocked=False side=caller
```

`channel_A` = gyroscope-vs-camera motion check passed. `channel_B` = screen-flash (colour challenge) confirmed a real face-like response.

**Verification policy (default):** the **gyroscope**, the **accelerometer** and the **illumination (screen flash)** checks run together and **any one passing verifies the session**. The log line shows `passes={'gyro':…,'accelerometer':…,'illumination':…}` and `verified_via=gyro|accelerometer|illumination`; call history shows e.g. *"Caller (you): verified via gyroscope"*. A covered camera or a screen / photo / glare reflection **blocks all three**. The gyro and accelerometer only count once the first flash has been scored (about 3 s), so a screen cannot verify on movement before the flash has a chance to recognise it. Set `LIVENESS_STRICT=1` on Render to require both movement and flash instead.

One line is logged per call outcome:

```
VERIFICATION_RESULT call=… outcome=verified both_channels=True …
VERIFICATION_RESULT call=… outcome=failed reason=<reason> side=<caller|callee> …
```

Failure reasons: `timeout`, `no_motion`, `flash_not_confirmed`, `reflection_detected`, `camera_blocked`, `no_telemetry`.

---

## TC01 — Valid phone number with correct OTP → authenticated
- **Do:** Log in with a real number, enter the OTP received by SMS.
- **App proof:** Home screen opens; Profile shows name and number.
- **Render proof:** search `AUTH_RESULT` → `action=login outcome=success phone=******1234` (number masked).

## TC02 — Phone number with incorrect OTP → rejected
- **Do:** Enter a wrong OTP.
- **App proof:** the app shows the OTP error and does not log in.
- **Where it is rejected:** OTP is verified **by Firebase on the phone** — the backend never sees the OTP. A wrong OTP therefore never reaches Render; the proof is the app screen (record it) and Firebase Console → Authentication → Users (no new sign-in).
- **Backend half:** an invalid/forged Firebase token is rejected: search `AUTH_RESULT` → `outcome=rejected` (HTTP 401).

## TC03 — Live user holding phone naturally → Channel A passes
- **Do:** Hold the phone naturally (normal hand tremor is enough) during verification.
- **Render proof:** search `verdict=ok` → lines with `S_A=` above 0.15 and `channel_A=True`.
- **Automated:** `test_the_slightest_shaking_verifies` (40 simulated sessions per intensity, ≥38/40 verify, down to 0.005 rad/s).

## TC04 — Static photograph held in front of camera → blocked
**What is checked (and what is not):** Channel A alone cannot tell a photo from a face — a photo moved by hand shifts in frame exactly as a face would. So a photo must be stopped by **Channel B** (flash response) and by the requirement that **both channels pass**.
- **Do (run all three; report each honestly):**
  1. Photo of a face **on a laptop/phone screen** held to the camera.
  2. A **glossy printed** photo.
  3. A **matte printed** photo.
  Hold the phone still for one run, and moving for another.
- **App proof:** verification times out → *"Couldn't verify it's really you in time"*; Call History shows **Likely Fraud** with the reason.
- **Render proof:** search `VERIFICATION_RESULT` → `outcome=failed reason=…`; and the verdict lines show `channel_B=False` (or `reflection_veto`).
- **Automated:** `test_tc04_photo_on_a_screen_moved_by_hand_is_not_trusted` (moving phone + screen photo → not trusted), `test_tc04_photo_with_a_still_phone_fails_both_channels`.
- **Known limit:** a matte print has a flat, skin-coloured flash response. The screen checks (flat + neutral colour, no depth parallax) are tuned on synthetic data; record the real result for each of the three photo types above and note it in this document.

## TC05 — Device held completely still → Channel A insufficient
> Under the default any-of-three policy a still device with a **real face** still verifies, via **illumination** (`verified_via=illumination`). To show a still device failing, point it at nothing face-like (or set `LIVENESS_STRICT=1`).
- **Do:** Put the phone on a stand/table so it cannot move; start verification with a real face in view.
- **Render proof:** `verdict=no_evidence` (no `S_A`), `channel_A=False`, `state=DEGRADED`; then `VERIFICATION_RESULT … reason=no_motion`.
- **App proof:** the app asks the user to move the phone ("motion prompt"), and after 30 s shows the failure.
- **Automated:** `test_tc05_still_phone_with_a_live_face_is_not_trusted`, `test_a_perfectly_still_phone_gives_no_evidence`.
- **Devices with no gyroscope** use the **accelerometer** (gravity removed, `accel_rms=` in the verdict line, threshold 0.03 m/s², a table-resting device reads about 0.005–0.02) or visible camera movement. Needs the app build that sends accelerometer data.
- **Phones with no gyroscope sensor** (common on budget phones): there is no gyro to read, so the camera image itself must visibly move (`video_motion=` in the verdict line, threshold 0.08 px per frame; a still camera reads ≤ 0.07). A still phone of this kind is rejected exactly like a gyro phone: `reason=no_motion`.
- **Note:** this holds in the default **strict** mode (both channels required). Set `LIVENESS_STRICT=0` on Render to fall back to either-channel trust.

## TC06 — Live user completes verification within 30 s → both channels pass
- **Do:** both users hold the phone naturally, face visible, in normal light.
- **Render proof:** `channel_A=True channel_B=True state=TRUSTED` on both `side=caller` and `side=callee`, then `VERIFICATION_RESULT … outcome=verified both_channels=True`.
- **App proof:** "Verified — secure call" badge and the video call opens.
- **Automated:** `test_tc06_both_channels_passing_is_trusted`.

## TC07 — Not completed within 30 s → timed out, blocked, fraud event logged
- **Do:** start verification and cover the camera, or keep the phone still, for 30 s.
- **App proof:** failure message; the call never opens.
- **Render proof:** `VERIFICATION_RESULT … outcome=failed reason=<…>` (WARNING level).
- **Automated:** `test_tc07_unverified_call_times_out_blocked_with_reason`.

## TC08 — Valid colour challenge on screen → brightness variation matches (Channel B passes)
- **Do:** verify normally; the screen flashes a random colour sequence.
- **Render proof:** `ISSUING challenge <id>` then `illum_verdict=ok S_illum=0.5…1.0`, `channel_B=True`.
- **Phone log:** `VerifyScreen[…]: verdict raw=…` shows the full flash features.
- **Automated:** `test_diffuse_face_like_response_passes`, `test_real_face_gets_confirmed_and_trusted`.

## TC09 — Fraud event after failed verification → saved and shown in call history
- **Do:** run TC07 (or TC04/TC05), then open **Call History** on either phone.
- **App proof:** the entry shows the other party, time, a **Likely Fraud** badge and one line per side, e.g. *"Caller (you): no movement detected"* and *"Receiver: verified"*. A verified call shows *"Caller: verified"* / *"Receiver: verified"* in green.
- **Render proof:** the `VERIFICATION_RESULT … outcome=failed reason=… side=…` line has the timestamp, failure mode and call id.
- **Database proof:** the call record stores `state=FAILED_VERIFICATION`, `created_at`, caller id and `failure_reason` holding both sides, e.g. `caller=no_motion,callee=verified` (Neon console → table `calls`).
- **Automated:** `test_failure_*` (reason classification).

---

## Honest limits (state these to the client)
- Thresholds were tuned on **synthetic** data and a small number of real phone runs. Real-world acceptance and false-reject rates are not yet measured on a large sample.
- A **matte printed photo** is the hardest attack for the flash channel; test and record it (TC04 step 3). A head-turn/blink challenge would harden this (needs an app update).
- Once a session reaches `TRUSTED` it stays trusted for that call (by design: no re-checks).
- Wrong-OTP handling (TC02) happens in Firebase, not the backend.
