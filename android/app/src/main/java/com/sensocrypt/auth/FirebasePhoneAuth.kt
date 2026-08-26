package com.sensocrypt.auth

import android.app.Activity
import com.google.firebase.auth.FirebaseAuth
import com.google.firebase.auth.PhoneAuthCredential
import com.google.firebase.auth.PhoneAuthOptions
import com.google.firebase.auth.PhoneAuthProvider
import kotlinx.coroutines.tasks.await
import java.util.concurrent.TimeUnit

/** Wraps Firebase Phone Auth's callback-based OTP flow (PhoneAuthProvider.verifyPhoneNumber
 * doesn't return a plain Task, unlike most other Firebase APIs, so it can't just be
 * `.await()`-ed like sign-in/getIdToken below can). OTP send + verify happens entirely
 * between this phone and Firebase -- the SensoCrypt backend never sees the code, never
 * sends an SMS, and never pays for one; it only ever sees the resulting ID token, which it
 * verifies server-side (see backend/app/core/firebase_auth.py). */
class FirebasePhoneAuth {
    private val auth = FirebaseAuth.getInstance()
    private var storedVerificationId: String? = null
    private var resendToken: PhoneAuthProvider.ForceResendingToken? = null

    /** phoneNumber must be E.164 format (e.g. "+919876543210"). onAutoVerified fires if
     * Android auto-detects the SMS and completes verification without the user typing
     * anything -- callers should treat that the same as a successful verifyOtp() call. */
    fun sendOtp(
        phoneNumber: String,
        activity: Activity,
        onCodeSent: () -> Unit,
        onAutoVerified: (credential: PhoneAuthCredential) -> Unit,
        onError: (String) -> Unit,
    ) {
        val callbacks = object : PhoneAuthProvider.OnVerificationStateChangedCallbacks() {
            override fun onVerificationCompleted(credential: PhoneAuthCredential) {
                onAutoVerified(credential)
            }

            override fun onVerificationFailed(e: com.google.firebase.FirebaseException) {
                onError(e.message ?: "Phone verification failed")
            }

            override fun onCodeSent(verificationId: String, token: PhoneAuthProvider.ForceResendingToken) {
                storedVerificationId = verificationId
                resendToken = token
                onCodeSent()
            }
        }

        val options = PhoneAuthOptions.newBuilder(auth)
            .setPhoneNumber(phoneNumber)
            .setTimeout(60L, TimeUnit.SECONDS)
            .setActivity(activity)
            .setCallbacks(callbacks)
            .build()
        PhoneAuthProvider.verifyPhoneNumber(options)
    }

    /** Call after the user types the SMS code. Returns the Firebase ID token to send to our
     * backend's /auth/phone/{signup,login}. Throws if sendOtp's onCodeSent hasn't fired yet
     * for this instance, or if the code is wrong (FirebaseAuthException). */
    suspend fun verifyOtp(code: String): String {
        val verificationId = storedVerificationId
            ?: throw IllegalStateException("No pending OTP -- call sendOtp first")
        val credential = PhoneAuthProvider.getCredential(verificationId, code)
        return signInWithCredential(credential)
    }

    suspend fun signInWithCredential(credential: PhoneAuthCredential): String {
        val result = auth.signInWithCredential(credential).await()
        val user = result.user ?: throw IllegalStateException("Firebase sign-in succeeded but returned no user")
        val tokenResult = user.getIdToken(false).await()
        return tokenResult.token ?: throw IllegalStateException("Firebase returned no ID token")
    }
}
