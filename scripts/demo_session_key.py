#!/usr/bin/env python3
"""Live walk-through of how the shared call session key is created and delivered.

Runs the project's OWN crypto functions (backend/app/core/crypto.py), in the same order the real
system uses them, and prints every step so it can be explained to an audience:

  1. each phone and the server agree a secret over an ephemeral X25519 exchange
  2. HKDF turns that secret into k_tel (telemetry) and k_chal (key-wrapping) for each phone
  3. ONLY after both phones are verified, the server mints ONE random 256-bit call key
  4. the server wraps (encrypts) that key separately for each phone under its own k_chal
  5. each phone unwraps it -> both hold the identical key, nobody in between can read it
  6. tampering / replaying against another call is rejected

Run:   cd backend && .venv/bin/python ../scripts/demo_session_key.py
Keys are shown as short SHA-256 fingerprints, never in full.
"""

import hashlib
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("PASETO_LOCAL_KEY_HEX", "00" * 32)  # not used here; the settings loader just needs a valid value

from cryptography.exceptions import InvalidTag  # noqa: E402

from app.core import crypto  # noqa: E402


def fp(b: bytes) -> str:
    """Short fingerprint: enough to compare keys on screen without revealing them."""
    return hashlib.sha256(b).hexdigest()[:12]


def step(n: int, title: str) -> None:
    print(f"\n[{n}] {title}\n" + "-" * 72)


call_id = str(uuid.uuid4())
print(f"call_id = {call_id}")

# Each phone has its own session (its own device login) -> its own key exchange with the server.
phones = {}
step(1, "Key exchange per phone: ephemeral X25519 (crypto.generate_x25519_keypair / x25519_agree)")
for side in ("caller", "callee"):
    session_id = str(uuid.uuid4())
    phone_priv, phone_pub = crypto.generate_x25519_keypair()          # on the phone
    server_priv, server_pub = crypto.generate_x25519_keypair()        # on the server (session.py /kex)
    z_server = crypto.x25519_agree(server_priv, phone_pub)            # server side of the agreement
    z_phone = crypto.x25519_agree(phone_priv, server_pub)             # phone side of the agreement
    assert z_server == z_phone, "both ends must derive the same shared secret"
    print(f"  {side:6s} session {session_id[:8]}  phone pub {fp(phone_pub)}  server pub {fp(server_pub)}")
    print(f"         shared secret fingerprint: server {fp(z_server)} == phone {fp(z_phone)}   (never sent over the network)")
    phones[side] = {"session_id": session_id, "z": z_server}

step(2, "HKDF-SHA256 splits it into two independent keys (crypto.derive_session_keys, salt = session_id)")
for side, p in phones.items():
    k_tel, k_chal = crypto.derive_session_keys(p["z"], p["session_id"])
    p["k_tel"], p["k_chal"] = k_tel, k_chal
    print(f"  {side:6s} k_tel {fp(k_tel)} (telemetry)   k_chal {fp(k_chal)} (wraps the call key)   different: {k_tel != k_chal}")
print("  The caller's and callee's keys differ: each phone has its own.")

step(3, "Liveness verification: BOTH phones must pass first (call_coordinator.mark_verified)")
print("  caller  -> verified via movement    callee -> verified via flash")
print("  Until both have passed, the server refuses to produce any key (get_or_create_session_key returns None).")

step(4, "Server mints ONE random 256-bit call key (crypto.generate_call_session_key = secrets.token_bytes(32))")
call_key = crypto.generate_call_session_key()
print(f"  call key fingerprint: {fp(call_key)}   (kept in server memory for this call only)")

step(5, "Server wraps it separately for each phone with AES-256-GCM under that phone's k_chal (crypto.wrap_call_session_key)")
wrapped = {}
for side, p in phones.items():
    wrapped[side] = crypto.wrap_call_session_key(call_key, p["k_chal"], call_id)
    print(f"  {side:6s} sends {len(wrapped[side])} bytes on the wire: 12-byte nonce + ciphertext+tag   ({fp(wrapped[side])})")
print(f"  The two wrapped blobs differ: {wrapped['caller'] != wrapped['callee']}  (different key, different random nonce)")

step(6, "Each phone unwraps its copy (Android: CallSessionCrypto.kt -> same AES-GCM) -> identical key on both")
got = {side: crypto.unwrap_call_session_key(wrapped[side], phones[side]["k_chal"], call_id) for side in phones}
for side, k in got.items():
    print(f"  {side:6s} recovered key fingerprint {fp(k)}")
assert got["caller"] == got["callee"] == call_key
print("  Both phones now hold the same call key; a network observer saw only the encrypted blobs.")

step(7, "Attacks that must fail")
try:
    crypto.unwrap_call_session_key(wrapped["caller"], phones["callee"]["k_chal"], call_id)
    print("  !! callee key opened caller's blob (BUG)")
except InvalidTag:
    print("  other phone's key cannot open it ........ rejected (authentication tag mismatch)")
try:
    crypto.unwrap_call_session_key(wrapped["caller"], phones["caller"]["k_chal"], str(uuid.uuid4()))
    print("  !! replayed against another call (BUG)")
except InvalidTag:
    print("  replay against a different call ......... rejected (call_id is bound in as AAD)")
tampered = bytearray(wrapped["caller"])
tampered[-1] ^= 1
try:
    crypto.unwrap_call_session_key(bytes(tampered), phones["caller"]["k_chal"], call_id)
    print("  !! tampered blob accepted (BUG)")
except InvalidTag:
    print("  one flipped bit in transit .............. rejected (AES-GCM integrity check)")

print("\nDone. Single-use + lifetime: k_chal is consumed on first fetch (nonce store) and the call key is dropped "
      "when the call ends (call_coordinator.clear).")
