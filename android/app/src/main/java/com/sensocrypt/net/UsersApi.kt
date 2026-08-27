package com.sensocrypt.net

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import okhttp3.Request

@Serializable
data class UserProfileResponse(val user_id: String, val name: String, val email: String, val phone_number: String)

class UsersApiException(val httpCode: Int, val body: String) : Exception("HTTP $httpCode: $body")

/** Talks to /api/v1/users/me. */
class UsersApi(private val baseUrl: String = "$BACKEND_HTTP_SCHEME://$BACKEND_HOST/api/v1") {
    private val client = OkHttpClient()
    private val json = Json { ignoreUnknownKeys = true }

    suspend fun getMyProfile(authToken: String): UserProfileResponse = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url("$baseUrl/users/me")
            .header("Authorization", "Bearer $authToken")
            .build()
        client.newCall(request).execute().use { response ->
            val text = response.body?.string().orEmpty()
            if (!response.isSuccessful) throw UsersApiException(response.code, text)
            json.decodeFromString(text)
        }
    }
}
