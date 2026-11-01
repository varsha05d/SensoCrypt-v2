# SensoCrypt v2 — performance metrics and thresholds

All values below are read from the code (`backend/app/liveness/*`, `core/call_coordinator.py`) as deployed.
"Simulated" figures come from the automated tests / simulations; "real" figures come from production logs.

## 1. Timing

| What | Value | Where |
|---|---|---|
| Time allowed to verify (both people) | **30 s** | `call_coordinator.VERIFY_WINDOW_S` |
| Camera → server frame rate | 15 fps, sent in **0.5 s** chunks | `TELEMETRY_FPS`, `CHUNK_MS` |
| Analysis window | **2.0 s**, re-scored every **0.5 s** | `WINDOW_S`, `HOP_S` |
| First flash challenge starts | ~**0.5 s** after capture begins (later ones every ~3 s) | `CHALLENGE_LEAD_S` |
| Flash length | 6 colour states × **300 ms** = **1.8 s** (red, green, blue, white, black + extras) | `STATE_DURATION_MS` |
| Flash scored | **0.3 s** after it ends | `CHALLENGE_END_MARGIN_S` |
| **Time to verify, ideal (simulated)** | **≈ 3.0 s** for gyro, accelerometer and flash alike | tests |
| Time to verify, real (observed) | **≈ 3–11 s** (one real call: gyro caller 10.8 s, including ~4 s of connection set-up before data flows) | Render logs |
| Gyro/accelerometer count only after | the first flash has been scored (or **7 s**) | `ILLUM_GRACE_S` |

## 2. Gyroscope check ("movement", Channel A)

| What | Value |
|---|---|
| Gyro starts counting as movement at | **0.003 rad/s** (RMS, 0.4–8 Hz band) — `MOTION_GATE` |
| Frequency band used | **0.4 – 8 Hz** (human hand movement), resampled to 50 Hz |
| A window scores as a pass when | gyro-vs-camera score `S_A` ≥ **0.15** — `P_TRUST` |
| Gyro passes when | **3 of the last 8** windows (≈ 4 s) pass — `CHANNEL_A_MIN_PASSES` |
| Axis-mapping lock needs | movement energy ≥ **0.006 rad/s** and match score ≥ **0.45** |
| Camera↔gyro delay searched | up to **±0.35 s** (`MAX_LAG_S`) |
| Real genuine values seen | `S_A` **0.27 – 0.47**, correlation `r` 0.43 – 0.66 |
| Simulated: slight shake 0.005–0.05 rad/s | verified **≥ 38 / 40** runs each, ~**3.0 s** |
| Simulated: tremor 0.003 rad/s | **50 / 50**, ~**4.5 s** |
| Simulated: phone lying still (noise ≤ 0.002 rad/s) | accepted **0 / 100** |
| Simulated: unrelated camera/gyro noise | accepted **13 – 21 %** (the accepted cost of the sensitivity) |

## 3. Accelerometer check ("movement", for devices without a gyro — or alongside it)

| What | Value |
|---|---|
| Counts as movement at | **0.03 m/s²** RMS after removing gravity (0.4–8 Hz) — `ACCEL_MIN_RMS` |
| Passes when | **3 of the last 6** checks (≈ 1.5 s of movement) over a **1.5 s** window |
| Device deemed gyro-less after | **1.5 s** of frames with zero gyro samples |
| **Real** resting-device noise | **≈ 0.015 m/s²** (about half the threshold) |
| Simulated: hand tremor 0.06 – 1.0 m/s² | detected **30 / 30** each; resting device (noise ≤ 0.02) **0 / 30** |

## 4. Flash check ("illumination", Channel B)

| What | Value |
|---|---|
| Frame analysed at | **160 × 120**, split into a **6 × 8** grid (48 cells) |
| Flash counted as a response when | correlation `r` ≥ **0.4** (`GLOBAL_R_MIN`) |
| A cell "responds" when | correlation ≥ **0.4** and gain ≥ **25 %** of the strongest cell |
| Flash confirms the person when | score `S_illum` ≥ **0.4** (`ILLUM_CONFIRM_MIN`); ≥ **0.15** counts as a weak pass |
| Weak-flash rule | if the whole picture barely changes (< **2** brightness levels), accept only if `r` ≥ **0.7** and ≥ **6** cells respond |
| Camera auto-exposure handled | exposure-corrected correlation, needs ≥ **6** responding cells |
| Latencies searched | 0, 33, 66, 100, 133, 166, 200 ms |
| Needs at least | **6** frames in the flash window |
| Real values seen | `S_illum` **0.41** (callee), **0.0 – 0.28** (caller; led to the weak-flash fix) |
| Simulated: genuine face, strong → very weak flash, with/without auto-exposure | confirmed **24 / 24** in every case |
| Simulated: no flash response / flat screen / glare spot | confirmed **0 / 24** |
| Simulated: flat scene + 3 % random ambient flicker | falsely confirmed **≈ 12 %** (same as before; a known limit) |

## 5. What blocks verification (overrides all three checks)

| Block | Trigger | Lasts |
|---|---|---|
| **Covered / black camera** | recent frames darker than **8** (0–255) or flatter than **6** | while it persists |
| **Glare / clipping** | > **5 %** of the picture blown out by the flash | **8 s** (a later clean flash clears it) |
| **Hot spot** | response in ≤ **2** cells holding ≥ **40 %** of the total | 8 s |
| **Mirrored screen** | very strong response (`r` ≥ 0.8) on under half a face's cells | 8 s |
| **Flat, colour-neutral screen** | even response (gain spread < **0.12** over ≥ **8** cells) **and** blue/red ratio ≥ **0.85** | 8 s |

## 6. Session and key

| What | Value |
|---|---|
| Call session key | **256-bit** random, created only after **both** sides verify |
| Key delivery | AES-256-GCM per phone; each blob **60 bytes** (12-byte nonce + 32-byte key + 16-byte tag) |
| Key exchange | X25519 + HKDF-SHA256 |
| Relay credential (TURN) | valid **30 days**, replacement minted **24 h** before expiry, only served after **3 min** |
| Automated tests | **105** backend tests, plus Android unit tests |

## 7. Honest limits
- The "simulated" figures use synthetic frames and sensor signals; real-phone rates at scale are not yet measured.
- Real numbers so far come from a few calls on two phones and one tablet.
- Real verification time is dominated by connection set-up and how well a phone's camera and gyro agree, not by the maths.
