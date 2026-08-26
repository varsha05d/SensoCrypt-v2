package com.sensocrypt.net

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

@Serializable data class SendOtpRequest(val phone_number: String)
@Serializable data class PhoneSignupRequest(val phone_number: String, val otp: String, val name: String, val email: String)
@Serializable data class PhoneLoginRequest(val phone_number: String, val otp: String)
@Serializable data class PhoneAuthResponse(val user_id: String, val token: String, val expires_in: Int)

class AuthPhoneApiException(val httpCode: Int, val body: String) : Exception("HTTP $httpCode: $body")

/** Talks to /api/v1/auth/phone/{send-otp,signup,login}. The OTP itself is sent and verified
 * by our own backend via MSG91 (see backend/app/core/msg91_auth.py) -- this app never talks
 * to MSG91 or sees anything beyond "code sent" / "code accepted or rejected". */
class AuthPhoneApi(private val baseUrl: String = "$BACKEND_HTTP_SCHEME://$BACKEND_HOST/api/v1/auth/phone") {
    private val client = OkHttpClient()
    private val json = Json { ignoreUnknownKeys = true }
    private val jsonMedia = "application/json; charset=utf-8".toMediaType()

    private suspend inline fun <reified Req, reified Res> post(path: String, body: Req): Res =
        withContext(Dispatchers.IO) {
            val requestBody = json.encodeToString(body).toRequestBody(jsonMedia)
            val request = Request.Builder().url("$baseUrl$path").post(requestBody).build()
            client.newCall(request).execute().use { response ->
                val text = response.body?.string().orEmpty()
                if (!response.isSuccessful) throw AuthPhoneApiException(response.code, text)
                json.decodeFromString(text)
            }
        }

    suspend fun sendOtp(phoneNumber: String): OkResponse =
        post("/send-otp", SendOtpRequest(phoneNumber))

    suspend fun signup(phoneNumber: String, otp: String, name: String, email: String): PhoneAuthResponse =
        post("/signup", PhoneSignupRequest(phoneNumber, otp, name, email))

    suspend fun login(phoneNumber: String, otp: String): PhoneAuthResponse =
        post("/login", PhoneLoginRequest(phoneNumber, otp))
}
