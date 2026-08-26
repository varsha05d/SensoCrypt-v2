"""ECDSA verification and PASETO session tokens (plan.md §4.3, §17.1).

TLS 1.3 secures the transport; this module is the application-layer crypto that
binds a request to the attested device key and issues short-lived session tokens.
Per plan.md §2: never roll your own transport crypto -- this only handles the
device-signature verification and PASETO issuance, nothing else.
"""

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone

import pyseto
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pyseto import Key

from app.core.config import settings


def build_auth_message(nonce: bytes, session_id: str, pubkey_der: bytes, channel_binding: bytes = b"") -> bytes:
    """m = H("SC-AUTH-v1" || nonce || session_id || H(pubkey) || EM), plan.md §4.3.

    channel_binding (EM) defaults to empty here: this dev backend runs over plain
    HTTP with no TLS termination in front of it yet, so there is no TLS exporter
    material to bind to. Wire this to the real tls-server-end-point binding
    (§4.5's practical note) before this protects against relay attacks (L4) --
    until then, both client and server must agree to pass b"" consistently.
    """
    parts = b"SC-AUTH-v1" + nonce + session_id.encode() + hashlib.sha256(pubkey_der).digest() + channel_binding
    return hashlib.sha256(parts).digest()


def verify_ecdsa_p256(pubkey_der: bytes, message: bytes, signature_der: bytes) -> bool:
    """Verify a SHA256withECDSA signature (DER-encoded) from an EC P-256 public key."""
    try:
        pubkey = serialization.load_der_public_key(pubkey_der)
    except ValueError:
        return False
    if not isinstance(pubkey, ec.EllipticCurvePublicKey):
        return False
    try:
        pubkey.verify(signature_der, message, ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False


def generate_x25519_keypair() -> tuple[x25519.X25519PrivateKey, bytes]:
    priv = x25519.X25519PrivateKey.generate()
    pub_raw = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return priv, pub_raw


def x25519_agree(private_key: x25519.X25519PrivateKey, peer_public_raw: bytes) -> bytes:
    peer_pub = x25519.X25519PublicKey.from_public_bytes(peer_public_raw)
    return private_key.exchange(peer_pub)


def derive_session_keys(shared_secret: bytes, session_id: str) -> tuple[bytes, bytes]:
    """plan.md §4.4: PRK = HKDF-Extract(salt=session_id, IKM=Z); k_tel/k_chal = HKDF-Expand(...).

    NOTE (scoped-down KEX): the plan's full SIGMA-I signs both ephemerals with the parties'
    long-term identity keys, requiring a second round trip so the client can sign over the
    server's freshly-returned epk_S. This backend instead binds the derived keys to
    `session_id`, which by this point in the flow already required a valid device-key
    signature to reach AUTHED state (§4.3). That is weaker than full SIGMA-I -- it does not
    protect the ephemeral exchange itself against an active MITM the way signing the
    ephemerals does -- and is a scoped-down step to keep this a single round trip. Worth
    tightening past prototype stage.
    """

    def hkdf(info: bytes) -> bytes:
        return HKDF(algorithm=hashes.SHA256(), length=32, salt=session_id.encode(), info=info).derive(shared_secret)

    return hkdf(b"sensocrypt/telemetry/v1"), hkdf(b"sensocrypt/challenge/v1")


def _local_key() -> Key:
    raw = bytes.fromhex(settings.paseto_local_key_hex)
    if len(raw) != 32:
        raise RuntimeError("PASETO_LOCAL_KEY_HEX must decode to exactly 32 bytes")
    return Key.new(version=4, purpose="local", key=raw)


def issue_session_token(device_id: str, session_id: str, ttl_s: int | None = None) -> str:
    ttl_s = ttl_s or settings.session_token_ttl_s
    now = datetime.now(timezone.utc)
    payload = {
        "device_id": device_id,
        "session_id": session_id,
        "iat": now.isoformat(),
        "exp": (now + timedelta(seconds=ttl_s)).isoformat(),
    }
    return pyseto.encode(_local_key(), payload).decode()


def verify_session_token(token: str) -> dict:
    decoded = pyseto.decode(_local_key(), token)
    payload = json.loads(decoded.payload)
    exp = datetime.fromisoformat(payload["exp"])
    if datetime.now(timezone.utc) >= exp:
        raise ValueError("session token expired")
    return payload


# --- User auth tokens (v2) --------------------------------------------------------------
# Separate from issue_session_token/verify_session_token above (which are scoped to a
# device+telemetry session) -- this is the app-level "you're logged in as this user" token
# issued after phone OTP verification, used to authorize placing calls etc.


def issue_user_token(user_id: str, ttl_s: int | None = None) -> str:
    ttl_s = ttl_s or settings.session_token_ttl_s
    now = datetime.now(timezone.utc)
    payload = {
        "user_id": user_id,
        "iat": now.isoformat(),
        "exp": (now + timedelta(seconds=ttl_s)).isoformat(),
    }
    return pyseto.encode(_local_key(), payload).decode()


def verify_user_token(token: str) -> dict:
    decoded = pyseto.decode(_local_key(), token)
    payload = json.loads(decoded.payload)
    exp = datetime.fromisoformat(payload["exp"])
    if datetime.now(timezone.utc) >= exp:
        raise ValueError("user token expired")
    return payload


# --- Shared call session key (v2) -------------------------------------------------------
# Once both parties on a call are independently liveness-verified, the backend mints ONE
# random key for that call_id and hands each side a copy, encrypted under that side's own
# already-established k_tel (from the existing X25519 kex flow, unchanged) so a network
# observer between either phone and the server can't read it. This is what the client asked
# for explicitly: a session key "generated in the backend," unique per call, that expires
# when the call ends -- not a peer-to-peer handshake between the two phones themselves.


def generate_call_session_key() -> bytes:
    return secrets.token_bytes(32)


def wrap_call_session_key(call_session_key: bytes, k_tel: bytes, call_id: str) -> bytes:
    """Encrypts the shared call key under one party's k_tel for delivery to them. Wire
    format: 12-byte random nonce || AES-256-GCM ciphertext, AAD binds it to this call_id so
    a copy can't be replayed against a different call."""
    nonce = secrets.token_bytes(12)
    aad = b"SC-CALLKEY-v1" + call_id.encode()
    ct = AESGCM(k_tel).encrypt(nonce, call_session_key, aad)
    return nonce + ct


def unwrap_call_session_key(wrapped: bytes, k_tel: bytes, call_id: str) -> bytes:
    nonce, ct = wrapped[:12], wrapped[12:]
    aad = b"SC-CALLKEY-v1" + call_id.encode()
    return AESGCM(k_tel).decrypt(nonce, ct, aad)
