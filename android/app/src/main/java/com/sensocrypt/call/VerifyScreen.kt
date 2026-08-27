package com.sensocrypt.call

import android.Manifest
import android.content.pm.PackageManager
import android.util.Base64
import android.util.Log
import android.util.Size
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.sensocrypt.capture.FrameCapture
import com.sensocrypt.capture.LiveStreamer
import com.sensocrypt.capture.Synchronizer
import com.sensocrypt.challenge.runChallengeFlash
import com.sensocrypt.crypto.KeystoreManager
import com.sensocrypt.crypto.encryptTelemetryChunk
import com.sensocrypt.crypto.unwrapCallSessionKey
import com.sensocrypt.identity.IdentityStore
import com.sensocrypt.identity.authenticateAndKex
import com.sensocrypt.net.AuthApi
import com.sensocrypt.net.CallsApi
import com.sensocrypt.net.SessionApi
import com.sensocrypt.net.TelemetrySocket
import com.sensocrypt.net.buildTelemetryChunkJson
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.util.concurrent.Executors

private const val TELEMETRY_CHUNK_INTERVAL_MS = 500L
// A little past the backend's own 30s window (call_coordinator.VERIFY_WINDOW_S) so a
// same-length client timeout doesn't race a legitimate last-second server verdict.
private const val OVERALL_TIMEOUT_MS = 35_000L

/**
 * Pre-connect liveness capture (v2): both the caller and the callee run this, independently,
 * against their own camera -- unlike v1's CallScreen, no WebRTC session exists yet, so frames
 * come from a dedicated CameraX ImageAnalysis pipeline (FrameCapture) instead of
 * WebRtcFrameSink. Reports into the backend's call_coordinator via TelemetrySocket's
 * call_id/side query params; once BOTH sides have reported TRUSTED, the backend hands back a
 * shared call session key, which this screen fetches, unwraps, and returns via onVerified --
 * only then does the caller move on to WebRTC/signaling (ConnectedCallScreen).
 */
@Composable
fun VerifyScreen(
    callId: String,
    side: String, // "caller" | "callee"
    onVerified: (sharedCallKey: ByteArray) -> Unit,
    onFailed: (reason: String) -> Unit,
    onCancel: () -> Unit,
) {
    val context = LocalContext.current

    var hasCameraPermission by remember {
        mutableStateOf(ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED)
    }
    val permissionLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.RequestPermission(),
    ) { granted -> hasCameraPermission = granted }

    LaunchedEffect(Unit) {
        if (!hasCameraPermission) permissionLauncher.launch(Manifest.permission.CAMERA)
    }

    if (!hasCameraPermission) {
        Box(modifier = Modifier.fillMaxSize().background(Color.Black), contentAlignment = Alignment.Center) {
            Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.padding(28.dp)) {
                Text(
                    "Camera access is needed to verify it's really you before this call connects.",
                    color = Color.White,
                    textAlign = TextAlign.Center,
                )
                Spacer(Modifier.height(16.dp))
                Button(onClick = { permissionLauncher.launch(Manifest.permission.CAMERA) }) { Text("Grant camera access") }
            }
        }
        return
    }

    VerifyScreenContent(callId = callId, side = side, onVerified = onVerified, onFailed = onFailed, onCancel = onCancel)
}

@Composable
private fun VerifyScreenContent(
    callId: String,
    side: String,
    onVerified: (ByteArray) -> Unit,
    onFailed: (String) -> Unit,
    onCancel: () -> Unit,
) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current

    val keystoreManager = remember { KeystoreManager(context) }
    val authApi = remember { AuthApi() }
    val sessionApi = remember { SessionApi() }
    val callsApi = remember { CallsApi() }
    val identityStore = remember { IdentityStore(context) }

    val synchronizer = remember { Synchronizer() }
    val liveStreamer = remember { LiveStreamer(context) }
    val analysisExecutor = remember { Executors.newSingleThreadExecutor() }

    var statusText by remember { mutableStateOf("Starting verification…") }
    var challengeFlashColor by remember { mutableStateOf<Color?>(null) }
    var retryAfterUnlock by remember { mutableStateOf<(() -> Unit)?>(null) }
    var settled by remember { mutableStateOf(false) }

    fun settle(block: () -> Unit) {
        if (settled) return
        settled = true
        block()
    }

    val keyguardLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        if (result.resultCode == android.app.Activity.RESULT_OK) retryAfterUnlock?.invoke()
    }

    LaunchedEffect(callId) {
        retryAfterUnlock = null
        val deviceId = identityStore.deviceId
        if (deviceId == null) {
            settle { onFailed("Device not set up") }
            return@LaunchedEffect
        }

        val overallDeadline = System.currentTimeMillis() + OVERALL_TIMEOUT_MS
        try {
            val handshake = authenticateAndKex(context, deviceId, keystoreManager, authApi, sessionApi, keyguardLauncher)
            val sessionId = handshake.challenge.session_id
            Log.i("SensoCrypt", "VerifyScreen[$side]: handshake OK, session_id=$sessionId, callId=$callId")
            val ws = TelemetrySocket(sessionId, quick = true, callId = callId, side = side)
            ws.connect()
            liveStreamer.start()

            launch {
                ws.lastVerdict.collect { text ->
                    val json = try { JSONObject(text) } catch (e: Exception) { return@collect }
                    json.optJSONObject("new_challenge")?.let { challenge ->
                        launch { runChallengeFlash(challenge) { challengeFlashColor = it } }
                    }
                }
            }

            var reachedTrusted = false
            while (System.currentTimeMillis() < overallDeadline && !settled) {
                delay(TELEMETRY_CHUNK_INTERVAL_MS)
                val chunk = liveStreamer.drainChunk()
                if (chunk.frames.isNotEmpty()) {
                    val plaintext = buildTelemetryChunkJson(chunk)
                    val encrypted = encryptTelemetryChunk(handshake.kTel, sessionId, chunk.seq, plaintext)
                    ws.send(encrypted)
                    Log.d("SensoCrypt", "VerifyScreen[$side]: sent chunk seq=${chunk.seq} frames=${chunk.frames.size} gyro=${chunk.gyro.size}")
                } else {
                    Log.d("SensoCrypt", "VerifyScreen[$side]: chunk seq=${chunk.seq} had 0 frames -- camera not producing frames yet")
                }

                val json = try { JSONObject(ws.lastVerdict.value) } catch (e: Exception) { null }
                val trustState = json?.optString("trust_state", "")
                Log.d("SensoCrypt", "VerifyScreen[$side]: verdict raw=${ws.lastVerdict.value}")
                // Must match the backend's own gate exactly (app/api/telemetry.py only calls
                // call_coordinator.mark_verified() when trust_state == "TRUSTED", not on any
                // softer p_trust threshold) -- showing "Verified" here on a looser condition
                // than what the server actually accepts just means polling for the session
                // key spins until the window times out, since the server never marked this
                // side verified in the first place.
                if (!reachedTrusted && trustState == "TRUSTED") {
                    reachedTrusted = true
                    statusText = "Verified — waiting for the other person…"
                }
                if (!reachedTrusted) statusText = "Verifying it's really you…"

                if (reachedTrusted) {
                    val keyResp = try { callsApi.getSessionKey(callId, sessionId, side) } catch (e: Exception) { null }
                    val wrappedB64 = keyResp?.wrapped_key_b64
                    if (wrappedB64 != null) {
                        val wrapped = Base64.decode(wrappedB64, Base64.NO_WRAP)
                        val sharedKey = unwrapCallSessionKey(wrapped, handshake.kChal, callId)
                        ws.close()
                        settle { onVerified(sharedKey) }
                        return@LaunchedEffect
                    }
                }
            }

            ws.close()
            Log.w("SensoCrypt", "VerifyScreen[$side]: timed out, reachedTrusted=$reachedTrusted")
            settle { onFailed(if (reachedTrusted) "The other person couldn't be verified in time" else "Couldn't verify it's really you in time") }
        } catch (e: Exception) {
            Log.e("SensoCrypt", "VerifyScreen[$side]: failed", e)
            settle { onFailed(e.message ?: "Verification failed") }
        }
    }

    DisposableEffect(Unit) {
        onDispose {
            liveStreamer.stop()
            analysisExecutor.shutdown()
        }
    }

    Box(modifier = Modifier.fillMaxSize().background(Color.Black)) {
        VerifyCameraFeed(
            synchronizer = synchronizer,
            executor = analysisExecutor,
            lifecycleOwner = lifecycleOwner,
            onFrame = { frame -> liveStreamer.onFrame(frame) },
        )

        Box(modifier = Modifier.fillMaxSize().background(Color.Black.copy(alpha = 0.45f)))

        Column(
            modifier = Modifier.fillMaxSize().padding(28.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Icon(Icons.Filled.Shield, contentDescription = null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.height(36.dp))
            Spacer(Modifier.height(16.dp))
            CircularProgressIndicator(color = MaterialTheme.colorScheme.primary)
            Spacer(Modifier.height(20.dp))
            Text(statusText, color = Color.White, textAlign = TextAlign.Center, style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(8.dp))
            Text(
                "Keep your face in view and hold the phone steady.",
                color = Color.White.copy(alpha = 0.7f),
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(28.dp))
            Button(onClick = { settle { onCancel() } }) { Text("Cancel") }
        }

        challengeFlashColor?.let { c -> Box(modifier = Modifier.fillMaxSize().background(c)) }
    }
}

@Composable
private fun VerifyCameraFeed(
    synchronizer: Synchronizer,
    executor: java.util.concurrent.ExecutorService,
    lifecycleOwner: androidx.lifecycle.LifecycleOwner,
    onFrame: (com.sensocrypt.capture.GrayFrame) -> Unit,
) {
    val context = LocalContext.current
    DisposableEffect(Unit) {
        val cameraProviderFuture = ProcessCameraProvider.getInstance(context)
        val listener = Runnable {
            val provider = cameraProviderFuture.get()
            val analysis = ImageAnalysis.Builder()
                .setTargetResolution(Size(320, 240))
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .build()
            analysis.setAnalyzer(executor, FrameCapture(synchronizer, onFrame))
            provider.unbindAll()
            try {
                provider.bindToLifecycle(lifecycleOwner, CameraSelector.DEFAULT_FRONT_CAMERA, analysis)
                Log.i("SensoCrypt", "VerifyScreen: camera bound OK")
            } catch (e: Exception) {
                Log.e("SensoCrypt", "VerifyScreen: camera bind failed", e)
            }
        }
        cameraProviderFuture.addListener(listener, ContextCompat.getMainExecutor(context))
        onDispose {
            cameraProviderFuture.get().unbindAll()
        }
    }
}
