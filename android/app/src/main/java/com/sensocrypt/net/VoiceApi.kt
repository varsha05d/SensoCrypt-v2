package com.sensocrypt.net

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

@Serializable
data class VoiceDetectionResult(val label: String, val confidence: Double)

class VoiceApiException(val httpCode: Int, val body: String) : Exception("HTTP $httpCode: $body")

/**
 * Talks to /api/v1/voice/detect (see backend/app/api/voice.py), which itself proxies to
 * a separate Cloud Run inference service -- so a single call here can be slow: that
 * service scales to zero, and a cold start plus RawNet2 inference can take several
 * seconds even before counting network time. In-progress feature, voice-detection branch
 * only; nothing on `main` calls this.
 */
class VoiceApi(private val baseUrl: String = "$BACKEND_HTTP_SCHEME://$BACKEND_HOST/api/v1") {
    // Measured cold: a scale-to-zero Cloud Run instance pulling its image + downloading
    // the model can take the backend's own httpx call the better part of a minute to
    // return -- a tighter client timeout here just means the request silently never
    // shows a result (observed directly during testing: the backend's own call to Cloud
    // Run succeeded well within its 60s budget, but this client had already given up).
    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(90, TimeUnit.SECONDS)
        .build()
    private val json = Json { ignoreUnknownKeys = true }

    suspend fun detect(wavBytes: ByteArray, authToken: String): VoiceDetectionResult = withContext(Dispatchers.IO) {
        val body = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart("file", "clip.wav", wavBytes.toRequestBody("audio/wav".toMediaType()))
            .build()
        val request = Request.Builder()
            .url("$baseUrl/voice/detect")
            .header("Authorization", "Bearer $authToken")
            .post(body)
            .build()
        client.newCall(request).execute().use { response ->
            val text = response.body?.string().orEmpty()
            if (!response.isSuccessful) throw VoiceApiException(response.code, text)
            json.decodeFromString(text)
        }
    }
}
