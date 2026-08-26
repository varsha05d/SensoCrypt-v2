package com.sensocrypt.crypto

import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * Unwraps the backend-generated, per-call shared session key (backend/app/core/crypto.py::
 * wrap_call_session_key). Wire format: 12-byte random nonce || AES-256-GCM ciphertext, AAD
 * binds it to this call_id -- must match unwrap_call_session_key byte-for-byte.
 */
fun unwrapCallSessionKey(wrapped: ByteArray, kChal: ByteArray, callId: String): ByteArray {
    val nonce = wrapped.copyOfRange(0, 12)
    val ciphertext = wrapped.copyOfRange(12, wrapped.size)
    val aad = "SC-CALLKEY-v1".toByteArray(Charsets.UTF_8) + callId.toByteArray(Charsets.UTF_8)

    val cipher = Cipher.getInstance("AES/GCM/NoPadding")
    cipher.init(Cipher.DECRYPT_MODE, SecretKeySpec(kChal, "AES"), GCMParameterSpec(128, nonce))
    cipher.updateAAD(aad)
    return cipher.doFinal(ciphertext)
}
