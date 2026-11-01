# SensoCrypt v2 — 6-minute demo script (all 9 test cases)

**Total: about 5½ minutes.** Two calls do most of the work: one that FAILS on purpose (TC04, TC07, TC09) and
one that SUCCEEDS (TC03, TC06, TC08). The 30-second wait in the failing call is used to explain, so no time is wasted.

## Before you start (not part of the 6 minutes)
- Open `https://sensocrypt-v2-backend.onrender.com/health` until it says `{"status":"ok"}` (free tier sleeps).
- Both phones logged in, latest APK on both, brightness up, **plugged in**.
- Render → Logs open on a laptop, search box ready. Times are UTC (IST − 5:30).
- A laptop showing a **face photo or face video** full screen (for the "attack").
- Screen-record the phones if you can. Do **one full rehearsal** first; read the "if it goes wrong" box below.

---

## 0:00 – 0:30 · Introduction (say this)
> "SensoCrypt makes sure the person on a video call is a **real, live person** before the call is allowed to
> start. Each phone proves it with two independent checks: **movement** — the phone's own sensors must agree
> with what the camera sees — and a **flash** — the screen flashes random colours and a real face reflects
> them in a particular way. Passing either check verifies you, and a screen, a photo or a covered camera
> blocks both. Both people must be verified within **30 seconds**, or the call never starts."

## 0:30 – 1:10 · TC01 + TC02 — login (40 s)
**Do:** log in with a valid number → enter the correct OTP → home screen opens. Log out, repeat with a **wrong OTP**.

| | Say | Show |
|---|---|---|
| **TC01** correct OTP | "A valid number with the right OTP is authenticated." | App: home screen / profile. Render: search `AUTH_RESULT` → `outcome=success phone=******1234` |
| **TC02** wrong OTP | "A wrong OTP is rejected. The OTP is checked by Firebase on the phone, so the backend never even sees it." | App: the error, no login. (Optional Render: `AUTH_RESULT … outcome=rejected` for an invalid token.) |

## 1:10 – 2:20 · The attack call — TC04, TC07, TC09 (70 s, includes the 30-second wait)
**Do:** Phone A calls Phone B; B accepts. **A points its camera at the laptop's face photo/video** and holds it
naturally. B (real face) just holds the phone. Start talking while the 30 s run.

**While the verification screen counts down, say (TC04):**
> "This is a presentation attack — a photo of a face held to the camera. The movement check alone can't tell a
> photo from a face, so the flash check is the one that catches it: a laptop screen or a print reflects the flash
> as a flat, even patch with neutral colour, where a real face reflects unevenly. The system recognises that and
> blocks it."

**At ~30 s the call fails. Say (TC07):**
> "Verification must finish within **30 seconds**. It didn't, so the session times out and the call is blocked —
> it never connects."

**Show (TC07 + TC09):**
- App: the "couldn't verify it's really you in time" message.
- Render: search `VERIFICATION_RESULT` → `outcome=failed reason=…` with the timestamp and call id.
- App → **Call History**: the entry has a red **Likely Fraud** badge and one line per side, e.g.
  *"Caller (you): screen or photo detected"* and *"Receiver: verified via movement"*.
> "(TC09) A fraud event is saved with the time, which side failed and why, and it's shown in call history."

## 2:20 – 3:40 · The genuine call — TC03, TC06, TC08 (80 s)
**Do:** Phone A calls B again, **both real faces in view, holding the phones naturally** (normal hand movement).

**Say while it verifies (~3–10 s):**
> "Now two real people. (TC03) Just holding a phone, the hand moves slightly — the gyroscope or accelerometer
> measures that, and the camera sees the same movement. They agree, so the movement check passes."

**Show (TC03, TC08) — Render:**
- `ISSUING challenge` — the random colour flash being sent (TC08).
- `illum_verdict=ok S_illum=0.4…1.0` — the camera saw brightness change matching the colours (TC08 passes).
- `passes={'gyro': True, …}` and `VERIFIED call=… side=caller via=gyro after=…s` (TC03).
- The app on a phone: the green notice under the badge, **"Verified via movement"** or **"Verified via flash"**, for 5 s.

**When both sides are verified the call opens. Say (TC06):**
> "(TC06) Both people verified within the 30 seconds. That's confirmed **both ways** — each side checks the other —
> and only then does the call connect, with its own one-time session key." — Render: `VERIFICATION_RESULT … outcome=verified`.

## 3:40 – 4:40 · TC05 — a phone held completely still (60 s)
**Do:** hang up. New call. **Put Phone A flat on a stand/table so it cannot move**, real face in view.

**Say:**
> "(TC05) Now the phone is completely still. The movement check has nothing to measure, so it correctly says
> *insufficient* — it needs real movement before it will vouch."

**Show — Render:** the verdict lines: `passes={'gyro': False, 'accelerometer': False, …}`,
`accel_rms=` near **0.01–0.02** (sensor noise) and `energy`/`S_A` absent.
> "Notice both movement checks are **False**. This phone can only be verified by the other check — the flash. If
> the flash didn't confirm a face either, nothing would pass and the call would be blocked, exactly like the
> photo before." — If it verifies via flash: point at the app notice **"Verified via flash"**.
**Bonus (10 s):** cover the camera with a finger → app says *"Camera is covered — uncover it…"*; log `camera_blocked=True`.

## 4:40 – 5:30 · Wrap-up (say this)
> "To summarise: login is verified by OTP (TC01, TC02). A real person passes through movement or flash
> (TC03, TC06, TC08). A still phone can't pass on movement (TC05). A photo or screen is blocked (TC04). If
> verification isn't done in 30 seconds the call is blocked (TC07), and every failure is recorded with a
> timestamp and the reason (TC09). The call key is created only after both people pass."
- Optional: show **Call History** with both entries side by side (red failure, green "verified via …").

---

## Test-case cheat sheet (for questions)
| TC | Shown in | Proof |
|---|---|---|
| 01 valid OTP | login | `AUTH_RESULT outcome=success` |
| 02 wrong OTP | login | app error (Firebase checks it) |
| 03 natural hold → movement passes | genuine call | `passes gyro/accelerometer True`, `VERIFIED … via=` |
| 04 photo / screen | attack call | `VERIFICATION_RESULT … failed`, "screen or photo detected" |
| 05 still device | still call | `passes gyro False, accelerometer False`, `accel_rms≈0.015` |
| 06 both verified in 30 s | genuine call | `VERIFICATION_RESULT outcome=verified` + call opens |
| 07 not done in 30 s → blocked | attack call | timeout message + `outcome=failed` |
| 08 colour flash matches | genuine call | `ISSUING challenge`, `illum_verdict=ok S_illum` |
| 09 fraud event saved | call history | timestamp, side, reason, "Likely Fraud" |

## If it goes wrong (keep calm — say this)
- **The photo/screen verified in the attack call:** "Screen detection depends on the lighting and angle; a
  matte print is the hardest case and is the next thing we'd harden." Then **move the phone closer to the laptop**
  and retry once. (Tested on simulated data; rehearse this one.)
- **Verification is slow (>10 s):** "Real phones take a few seconds to settle." Keep talking; the window is 30 s.
- **No video after connecting:** the call relay needs about 3–4 minutes after the backend wakes. Open `/health` early.
- **Render logs lag:** refresh the log search; entries appear within a second or two.
- **A phone has no gyro (tablet):** "It uses its accelerometer instead" — the log shows `gyro_unavailable=True`.

## Honest limits (if asked)
- Thresholds were tuned mostly on simulated data and a few real phones; real-world rates aren't measured at scale.
- A matte printed photo is the hardest attack for the flash check; a blink/head-turn challenge would harden it.
- The shared call key is delivered to both phones; call media itself is protected by WebRTC's built-in encryption.
