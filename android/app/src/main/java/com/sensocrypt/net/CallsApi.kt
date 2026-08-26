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

@Serializable data class OkResponse(val ok: Boolean)
@Serializable data class FcmTokenRequest(val fcm_token: String)
@Serializable data class PlaceCallRequest(val callee_phone_number: String)
@Serializable data class PlaceCallResponse(val call_id: String)
@Serializable data class AcceptCallResponse(val ok: Boolean, val verify_window_s: Double)
@Serializable data class SessionKeyRequest(val session_id: String, val side: String)
@Serializable data class SessionKeyResponse(val wrapped_key_b64: String? = null)
@Serializable
data class CallLogEntry(
    val call_id: String,
    val other_party_name: String,
    val other_party_phone: String,
    val direction: String,
    val state: String,
    val created_at: String,
)

class CallsApiException(val httpCode: Int, val body: String) : Exception("HTTP $httpCode: $body")

/** Talks to /api/v1/{users/me/fcm-token, calls, calls/logs} -- everything here except
 * /session-key requires the user auth token from AuthPhoneApi (see identity/UserSession.kt),
 * sent as `Authorization: Bearer <token>`. */
class CallsApi(private val baseUrl: String = "$BACKEND_HTTP_SCHEME://$BACKEND_HOST/api/v1") {
    private val client = OkHttpClient()
    private val json = Json { ignoreUnknownKeys = true }
    private val jsonMedia = "application/json; charset=utf-8".toMediaType()

    private suspend inline fun <reified Req, reified Res> post(path: String, body: Req, authToken: String? = null): Res =
        withContext(Dispatchers.IO) {
            val requestBody = json.encodeToString(body).toRequestBody(jsonMedia)
            val builder = Request.Builder().url("$baseUrl$path").post(requestBody)
            if (authToken != null) builder.header("Authorization", "Bearer $authToken")
            client.newCall(builder.build()).execute().use { response ->
                val text = response.body?.string().orEmpty()
                if (!response.isSuccessful) throw CallsApiException(response.code, text)
                json.decodeFromString(text)
            }
        }

    private suspend inline fun <reified Res> postEmpty(path: String, authToken: String): Res =
        withContext(Dispatchers.IO) {
            val request = Request.Builder()
                .url("$baseUrl$path")
                .post("".toRequestBody(jsonMedia))
                .header("Authorization", "Bearer $authToken")
                .build()
            client.newCall(request).execute().use { response ->
                val text = response.body?.string().orEmpty()
                if (!response.isSuccessful) throw CallsApiException(response.code, text)
                json.decodeFromString(text)
            }
        }

    private suspend inline fun <reified Res> get(path: String, authToken: String): Res =
        withContext(Dispatchers.IO) {
            val request = Request.Builder().url("$baseUrl$path").header("Authorization", "Bearer $authToken").build()
            client.newCall(request).execute().use { response ->
                val text = response.body?.string().orEmpty()
                if (!response.isSuccessful) throw CallsApiException(response.code, text)
                json.decodeFromString(text)
            }
        }

    suspend fun setFcmToken(fcmToken: String, authToken: String): OkResponse =
        post("/users/me/fcm-token", FcmTokenRequest(fcmToken), authToken)

    suspend fun placeCall(calleePhoneNumber: String, authToken: String): PlaceCallResponse =
        post("/calls", PlaceCallRequest(calleePhoneNumber), authToken)

    suspend fun acceptCall(callId: String, authToken: String): AcceptCallResponse =
        postEmpty("/calls/$callId/accept", authToken)

    /** No auth token needed -- the shared key is gated on call_coordinator's own
     * verification state server-side, not on who's asking (see backend's docstring). */
    suspend fun getSessionKey(callId: String, sessionId: String, side: String): SessionKeyResponse =
        post("/calls/$callId/session-key", SessionKeyRequest(sessionId, side))

    suspend fun callLogs(authToken: String): List<CallLogEntry> = get("/calls/logs", authToken)
}
