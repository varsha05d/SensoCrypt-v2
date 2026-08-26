package com.sensocrypt.auth

import com.msg91.sendotp.OTPWidget
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject

private const val WIDGET_ID = "36687a6f666b373435333637"
private const val TOKEN_AUTH = "564712T8EqkhspW6a8f0947P1"

class Msg91AuthException(message: String) : Exception(message)

/** Wraps MSG91's OTPWidget Kotlin SDK (com.msg91.lib:sendotp) -- OTP send, entry, and
 * verify all happen on-device against MSG91's own default (non-DLT-registered) template.
 * Our backend never sends an OTP itself; it only verifies the resulting access token
 * server-side (see backend/app/core/msg91_auth.py) before trusting the phone number it
 * carries. WIDGET_ID/TOKEN_AUTH are not secret -- they're the client-side identifiers
 * MSG91's own SDK expects to ship inside the app, analogous to a Firebase apiKey. */
class Msg91PhoneAuth {
    private var reqId: String = ""

    /** phoneNumberNoPlus must be digits only with country code, no leading '+'
     * (e.g. "919876543210"). */
    suspend fun sendOtp(phoneNumberNoPlus: String) {
        val result: String = withContext(Dispatchers.IO) {
            OTPWidget.sendOTP(WIDGET_ID, TOKEN_AUTH, phoneNumberNoPlus)
        }
        val json = JSONObject(result)
        val type = json.optString("type")
        val message = json.optString("message")
        if (type == "error" || message.isEmpty()) {
            throw Msg91AuthException(message.ifEmpty { "Failed to send OTP" })
        }
        reqId = message
    }

    /** Returns the access token (JWT) to send to our backend's
     * /auth/phone/{signup,login}. */
    suspend fun verifyOtp(otp: String): String {
        val result: String = withContext(Dispatchers.IO) {
            OTPWidget.verifyOTP(WIDGET_ID, TOKEN_AUTH, reqId, otp)
        }
        val json = JSONObject(result)
        val type = json.optString("type")
        val message = json.optString("message")
        if (type == "error") {
            throw Msg91AuthException(message.ifEmpty { "Incorrect or expired code" })
        }
        return message
    }
}
