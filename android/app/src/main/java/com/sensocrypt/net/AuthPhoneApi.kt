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

@Serializable data class PhoneSignupRequest(val firebase_id_token: String, val name: String, val email: String)
@Serializable data class PhoneLoginRequest(val firebase_id_token: String)
@Serializable data class PhoneAuthResponse(val user_id: String, val token: String, val expires_in: Int)

class AuthPhoneApiException(val httpCode: Int, val body: String) : Exception("HTTP $httpCode: $body")

/** Talks to /api/v1/auth/phone/{signup,login} -- the client already verified the phone
 * number's OTP directly with Firebase (see auth/FirebasePhoneAuth.kt) before ever calling
 * this; what's sent here is the resulting Firebase ID token, which the backend verifies
 * server-side. */
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

    suspend fun signup(firebaseIdToken: String, name: String, email: String): PhoneAuthResponse =
        post("/signup", PhoneSignupRequest(firebaseIdToken, name, email))

    suspend fun login(firebaseIdToken: String): PhoneAuthResponse =
        post("/login", PhoneLoginRequest(firebaseIdToken))
}
