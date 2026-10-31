#!/usr/bin/env python3
"""
SensoCrypt -- how a call's session key is generated (self-contained demo, one file).

This file contains the actual logic, not a description of it. It uses the same algorithms and the same
constants as the real system (backend/app/core/crypto.py, call_coordinator.py, api/calls.py and the Android
app), so what you see here is what happens on a real call.

    Run it :   python3 session_key_demo.py            (a normal call: both people verified)
               python3 session_key_demo.py --fail     (one person fails verification: NO key is made)
    Needs  :   Python 3 and the `cryptography` package   (pip install cryptography)

THE IDEA IN FIVE LINES
    1. Each phone does a key exchange with the server, so both ends share a secret nobody else knows.
    2. That secret is split into two keys:  k_tel (telemetry) and k_chal (protects the call key).
    3. The server refuses to create a call key until BOTH people on the call pass liveness verification.
    4. Then the server makes ONE random 256-bit call key and encrypts a separate copy for each phone.
    5. Each phone decrypts its copy: both now hold the same key, and nobody in between can read it.
"""

import hashlib
import secrets
import sys
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


# =============================================================================================
#  THE CRYPTOGRAPHY  (identical to backend/app/core/crypto.py)
# =============================================================================================

def new_keypair():
    """A fresh, throw-away X25519 key pair. 'Ephemeral' = used once, for this session only."""
    private = x25519.X25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)   # 32 bytes, safe to send
    return private, public


def agree(my_private, their_public: bytes) -> bytes:
    """X25519 Diffie-Hellman. Both sides compute the SAME secret from (own private, other's public)
    without that secret ever travelling over the network."""
    return my_private.exchange(x25519.X25519PublicKey.from_public_bytes(their_public))


def derive_keys(shared_secret: bytes, session_id: str):
    """HKDF-SHA256: stretches the shared secret into two independent 256-bit keys.
    The different `info` labels guarantee k_tel and k_chal can never be equal."""
    def hkdf(info: bytes) -> bytes:
        return HKDF(algorithm=hashes.SHA256(), length=32, salt=session_id.encode(), info=info).derive(shared_secret)
    return hkdf(b"sensocrypt/telemetry/v1"), hkdf(b"sensocrypt/challenge/v1")      # (k_tel, k_chal)


def make_call_key() -> bytes:
    """The shared call session key: 32 random bytes from the operating system's secure random source."""
    return secrets.token_bytes(32)


def wrap(call_key: bytes, k_chal: bytes, call_id: str) -> bytes:
    """Encrypt the call key for ONE phone: AES-256-GCM under that phone's k_chal.
    On the wire:  12-byte random nonce || ciphertext+tag.  The call_id is mixed in as 'associated data',
    so a copy made for one call cannot be replayed on another."""
    nonce = secrets.token_bytes(12)
    return nonce + AESGCM(k_chal).encrypt(nonce, call_key, b"SC-CALLKEY-v1" + call_id.encode())


def unwrap(blob: bytes, k_chal: bytes, call_id: str) -> bytes:
    """The phone's side: decrypt + verify. Raises InvalidTag if the key, call_id or any bit is wrong."""
    return AESGCM(k_chal).decrypt(blob[:12], blob[12:], b"SC-CALLKEY-v1" + call_id.encode())


# =============================================================================================
#  THE SERVER'S RULES  (call_coordinator.py + api/calls.py)
# =============================================================================================

class CallServer:
    """What the backend does for one call."""

    def __init__(self, call_id: str):
        self.call_id = call_id
        self.verified = {}          # side -> how it was verified ("movement" / "flash"), or None
        self.sessions = {}          # side -> k_chal (from that phone's key exchange)
        self.call_key = None

    def key_exchange(self, side: str, phone_public: bytes, session_id: str) -> bytes:
        """POST /session/kex : server makes its own key pair, agrees the secret, derives the keys."""
        server_private, server_public = new_keypair()
        shared = agree(server_private, phone_public)
        _k_tel, k_chal = derive_keys(shared, session_id)
        self.sessions[side] = k_chal
        return server_public        # only PUBLIC keys cross the network

    def report(self, side: str, passed: bool, how: str = ""):
        """Liveness result from that phone's telemetry stream (gyro / accelerometer / flash)."""
        self.verified[side] = how if passed else None

    def deliver_key(self, side: str):
        """POST /calls/{id}/session-key : the key is created only when BOTH sides are verified."""
        if not (self.verified.get("caller") and self.verified.get("callee")):
            return None                                       # refused: nobody gets a key
        if self.call_key is None:
            self.call_key = make_call_key()                   # created once, shared by both phones
        return wrap(self.call_key, self.sessions[side], self.call_id)


# =============================================================================================
#  THE DEMO
# =============================================================================================

def fp(b: bytes) -> str:
    """Short fingerprint so keys can be compared on screen without printing them."""
    return hashlib.sha256(b).hexdigest()[:12]


def title(n, text):
    print(f"\n[{n}] {text}\n" + "-" * 78)


def main():
    fail = "--fail" in sys.argv
    call_id = str(uuid.uuid4())
    server = CallServer(call_id)
    print(f"SensoCrypt session-key demo      call_id = {call_id}")
    print("(keys are shown only as short fingerprints, never in full)")

    # ---- 1. key exchange, once per phone -------------------------------------------------------------
    title(1, "Each phone does a key exchange with the server (X25519)")
    phones = {}
    for side in ("caller", "callee"):
        session_id = str(uuid.uuid4())
        phone_private, phone_public = new_keypair()
        server_public = server.key_exchange(side, phone_public, session_id)
        phone_secret = agree(phone_private, server_public)
        phone_tel, phone_chal = derive_keys(phone_secret, session_id)
        phones[side] = {"k_chal": phone_chal}
        print(f"  {side:6s}  phone public {fp(phone_public)}  <->  server public {fp(server_public)}")
        print(f"          phone's shared secret fingerprint: {fp(phone_secret)}   (the server computed the same one)")
        print(f"          keys derived with HKDF:  k_tel {fp(phone_tel)}   k_chal {fp(phone_chal)}")
        assert phone_chal == server.sessions[side], "phone and server must end up with the same k_chal"
    print("  -> Each phone now shares a private key with the server. The secret itself never crossed the network.")
    print(f"  -> The two phones have DIFFERENT keys: {phones['caller']['k_chal'] != phones['callee']['k_chal']}")

    # ---- 2. the gate ---------------------------------------------------------------------------------
    title(2, "Liveness verification decides whether a key may exist")
    server.report("caller", True, "movement")
    print("  caller  verified via movement")
    blob = server.deliver_key("caller")
    print(f"  caller asks for the key now -> {'REFUSED (callee not verified yet)' if blob is None else '!! issued (BUG)'}")
    if fail:
        server.report("callee", False)
        print("  callee  FAILED verification (e.g. covered camera, or a photo / screen was shown)")
    else:
        server.report("callee", True, "flash")
        print("  callee  verified via flash")

    # ---- 3. key creation + delivery ------------------------------------------------------------------
    title(3, "Server creates ONE random 256-bit call key and encrypts a copy for each phone")
    blobs = {side: server.deliver_key(side) for side in ("caller", "callee")}
    if server.call_key is None:
        print("  No key was created. Nobody can start the call, and nothing was sent to either phone.")
        print("\nResult: a call is only ever given a session key after BOTH people pass verification.")
        return
    print(f"  call key fingerprint on the server: {fp(server.call_key)}   (kept in memory for this call only)")
    for side, b in blobs.items():
        print(f"  {side:6s} receives {len(b)} bytes: 12-byte nonce + AES-256-GCM ciphertext   [{fp(b)}]")
    print(f"  The two encrypted copies are different: {blobs['caller'] != blobs['callee']}"
          "   (different key + different random nonce)")

    # ---- 4. phones decrypt ---------------------------------------------------------------------------
    title(4, "Each phone decrypts its copy: both now hold the same call key")
    got = {side: unwrap(blobs[side], phones[side]["k_chal"], call_id) for side in blobs}
    for side, key in got.items():
        print(f"  {side:6s} recovered key fingerprint {fp(key)}")
    assert got["caller"] == got["callee"] == server.call_key
    print("  -> identical on both phones. Anyone watching the network saw only the encrypted copies.")

    # ---- 5. attacks ----------------------------------------------------------------------------------
    title(5, "What an attacker cannot do")
    tests = [
        ("use the OTHER phone's key to open it", lambda: unwrap(blobs["caller"], phones["callee"]["k_chal"], call_id)),
        ("replay the copy on a DIFFERENT call", lambda: unwrap(blobs["caller"], phones["caller"]["k_chal"], str(uuid.uuid4()))),
        ("flip a single bit in transit", lambda: unwrap(blobs["caller"][:-1] + bytes([blobs["caller"][-1] ^ 1]), phones["caller"]["k_chal"], call_id)),
    ]
    for what, attempt in tests:
        try:
            attempt()
            print(f"  !! {what}: SUCCEEDED (this would be a bug)")
        except InvalidTag:
            print(f"  {what:44s} -> rejected")

    print("\nResult: one random key per call, created only after both people are verified, delivered encrypted,")
    print("        tamper-proof, and discarded when the call ends.")


if __name__ == "__main__":
    main()
