package com.sensocrypt.net

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import org.webrtc.PeerConnection

/** Fetches the STUN/TURN list from the backend, so TURN credentials stay server-side. */
class IceApi(private val baseUrl: String = "$BACKEND_HTTP_SCHEME://$BACKEND_HOST/api/v1") {
    private val client = OkHttpClient()

    /** Null on any failure -- the caller falls back to STUN only. */
    suspend fun getIceServers(authToken: String): List<PeerConnection.IceServer>? = withContext(Dispatchers.IO) {
        try {
            val request = Request.Builder()
                .url("$baseUrl/ice-servers")
                .header("Authorization", "Bearer $authToken")
                .build()
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return@withContext null
                val arr = JSONObject(response.body?.string().orEmpty()).getJSONArray("ice_servers")
                (0 until arr.length()).map { i ->
                    val s = arr.getJSONObject(i)
                    val urls = s.get("urls").let { u ->
                        if (u is org.json.JSONArray) (0 until u.length()).map { u.getString(it) } else listOf(u.toString())
                    }
                    PeerConnection.IceServer.builder(urls).apply {
                        if (s.has("username")) setUsername(s.getString("username"))
                        if (s.has("credential")) setPassword(s.getString("credential"))
                    }.createIceServer()
                }
            }
        } catch (e: Exception) {
            null
        }
    }
}
