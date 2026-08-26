"""plan.md §17.4 crypto/ acceptance tests -- the ones testable without a real
Android device's attestation bytes on hand. Attestation-chain parsing (rejects
unlocked bootloader / wrong package / wrong signing digest / stale challenge /
software-only key) needs a real or fixture-captured cert chain and is exercised
manually against a physical device during Phase 2's build-out.
"""

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.hashes import SHA256

from app.core import crypto


def _gen_keypair():
    priv = ec.generate_private_key(ec.SECP256R1())
    pub_der = priv.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return priv, pub_der


def test_signature_verifies_with_correct_key():
    priv, pub_der = _gen_keypair()
    message = crypto.build_auth_message(b"n" * 32, "session-1", pub_der)
    sig = priv.sign(message, ec.ECDSA(SHA256()))
    assert crypto.verify_ecdsa_p256(pub_der, message, sig)


def test_signature_bound_to_channel():
    """valid sig, wrong EM -> verification fails (plan.md §17.4)."""
    priv, pub_der = _gen_keypair()
    message = crypto.build_auth_message(b"n" * 32, "session-1", pub_der, channel_binding=b"real-channel")
    sig = priv.sign(message, ec.ECDSA(SHA256()))

    tampered_message = crypto.build_auth_message(b"n" * 32, "session-1", pub_der, channel_binding=b"attacker-channel")
    assert not crypto.verify_ecdsa_p256(pub_der, tampered_message, sig)


def test_signature_rejected_with_wrong_key():
    _, pub_der_signer = _gen_keypair()
    priv_attacker, _ = _gen_keypair()

    message = crypto.build_auth_message(b"n" * 32, "session-1", pub_der_signer)
    forged_sig = priv_attacker.sign(message, ec.ECDSA(SHA256()))
    assert not crypto.verify_ecdsa_p256(pub_der_signer, message, forged_sig)


def test_session_token_roundtrip(monkeypatch):
    monkeypatch.setattr(crypto.settings, "paseto_local_key_hex", "00" * 32)
    token = crypto.issue_session_token(device_id="dev-1", session_id="sess-1", ttl_s=60)
    payload = crypto.verify_session_token(token)
    assert payload["device_id"] == "dev-1"
    assert payload["session_id"] == "sess-1"


def test_expired_session_token_rejected(monkeypatch):
    monkeypatch.setattr(crypto.settings, "paseto_local_key_hex", "00" * 32)
    token = crypto.issue_session_token(device_id="dev-1", session_id="sess-1", ttl_s=-1)
    with pytest.raises(ValueError):
        crypto.verify_session_token(token)


def test_user_token_roundtrip(monkeypatch):
    monkeypatch.setattr(crypto.settings, "paseto_local_key_hex", "00" * 32)
    token = crypto.issue_user_token(user_id="user-1", ttl_s=60)
    payload = crypto.verify_user_token(token)
    assert payload["user_id"] == "user-1"


def test_expired_user_token_rejected(monkeypatch):
    monkeypatch.setattr(crypto.settings, "paseto_local_key_hex", "00" * 32)
    token = crypto.issue_user_token(user_id="user-1", ttl_s=-1)
    with pytest.raises(ValueError):
        crypto.verify_user_token(token)


def test_call_session_key_wrap_roundtrip():
    """Both sides get the same shared key back after wrap/unwrap under their own k_tel-like
    key -- this is the "backend generates a unique session key" mechanism the client asked
    for (see crypto.wrap_call_session_key's docstring)."""
    call_key = crypto.generate_call_session_key()
    k_side_a = b"a" * 32
    wrapped_for_a = crypto.wrap_call_session_key(call_key, k_side_a, call_id="call-123")
    assert crypto.unwrap_call_session_key(wrapped_for_a, k_side_a, call_id="call-123") == call_key


def test_call_session_key_rejects_wrong_call_id():
    """A wrapped key can't be replayed against a different call_id -- the AAD binds it."""
    call_key = crypto.generate_call_session_key()
    k_side_a = b"a" * 32
    wrapped = crypto.wrap_call_session_key(call_key, k_side_a, call_id="call-123")
    with pytest.raises(Exception):  # cryptography raises InvalidTag
        crypto.unwrap_call_session_key(wrapped, k_side_a, call_id="call-456")


def test_call_session_key_rejects_wrong_key():
    call_key = crypto.generate_call_session_key()
    wrapped = crypto.wrap_call_session_key(call_key, b"a" * 32, call_id="call-123")
    with pytest.raises(Exception):  # cryptography raises InvalidTag
        crypto.unwrap_call_session_key(wrapped, b"b" * 32, call_id="call-123")


def test_call_session_keys_are_unique():
    keys = {crypto.generate_call_session_key() for _ in range(100)}
    assert len(keys) == 100
