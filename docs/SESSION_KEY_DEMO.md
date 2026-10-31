# Session key: where it is generated and how to show it

## The flow in one picture

```
Phone (each side)                         Backend                                   Where in code
─────────────────                         ───────                                   ─────────────
1. generate ephemeral X25519 pair  ─pub─▶  generate its own X25519 pair             crypto.py:53  session.py:20-33
   agree shared secret Z           ◀─pub─  agree the same Z (Z never sent)          crypto.py:59
2. HKDF(Z, salt=session_id) → k_tel, k_chal      (same on both ends)                crypto.py:64   (Android: SessionCrypto.kt)
3. each phone runs liveness checks (gyro / accelerometer / flash)  ─telemetry─▶     call_coordinator.mark_verified
4.                                          BOTH verified? → mint ONE random        call_coordinator.py:188-199
                                            256-bit call key                        crypto.py:146  (secrets.token_bytes(32))
5.                                          wrap it for each phone: AES-256-GCM     calls.py:176-192  crypto.py:150
                                            under that phone's k_chal, AAD=call_id
6. phone fetches (POST /calls/{id}/session-key) and unwraps                         CallSessionCrypto.kt, VerifyScreen.kt:228
```

Key properties: generated **in the backend**, one per call, only after **both** sides are verified, delivered
encrypted separately to each phone, k_chal is single-use, and the key is dropped when the call ends.

## Live demo (3 minutes)

1. **Run the walk-through** (real project functions, every step printed, keys shown as fingerprints):
   ```bash
   cd backend && .venv/bin/python ../scripts/demo_session_key.py
   ```
   Point out: shared secrets match on both ends; the two phones get different k_chal; one call key; both
   recover the same fingerprint; the three attacks are rejected.
2. **Show the real system doing it** (Render → Logs, search these, in this order, during a call):
   - `POST /api/v1/session/kex` → step 1-2, one per phone (key exchange)
   - `VERIFIED call=… side=… via=…` → step 3 (both sides must appear)
   - `POST /api/v1/calls/<id>/session-key 200` → step 6. It is polled until the key exists; the key is only
     produced after both `VERIFIED` lines, then both phones fetch it once.
   - `VERIFICATION_RESULT … outcome=verified`
3. **Show the refusal case**: let one phone fail verification (cover the camera). The log shows
   `VERIFICATION_RESULT … outcome=failed` and **no key is ever minted** for that call.
4. **Open the code** at the lines in the table above.

## Be ready for these questions (honest answers)

- *Is the call media encrypted with this key?* **Not currently.** The key is delivered and unwrapped on both
  phones, but the app does not yet use it to encrypt audio/video. Call media is protected by WebRTC's own
  DTLS-SRTP (always on). Using the shared key (for example for an application-level check or extra layer) is a
  next step.
- *Why not a phone-to-phone handshake?* The brief asked for a backend-generated, per-call key that expires with
  the call. The trade-off is that the backend briefly holds the key.
- *Weak point of the key exchange?* It is a scoped-down version of the planned SIGMA-I design: keys are bound
  to a session that already proved a device key, but the ephemeral exchange itself is not signed, so an active
  man-in-the-middle on that step is not fully prevented (noted in `crypto.py:64`).
