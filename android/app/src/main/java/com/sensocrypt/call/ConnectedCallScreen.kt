package com.sensocrypt.call

import android.Manifest
import android.content.pm.PackageManager
import android.util.Log
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.CallEnd
import androidx.compose.material.icons.filled.Cameraswitch
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.MicOff
import androidx.compose.material.icons.filled.RecordVoiceOver
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import com.sensocrypt.net.SignalSocket
import com.sensocrypt.net.VoiceApi
import kotlinx.coroutines.launch
import org.json.JSONObject
import org.webrtc.EglBase
import org.webrtc.IceCandidate
import org.webrtc.SessionDescription
import org.webrtc.SurfaceViewRenderer

private val CALL_PERMISSIONS = arrayOf(Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO)

/** Latest AI-vs-human verdict on the other party's voice (see VoiceDetectionRecorder) --
 * null until the first ~4-second window has been analyzed. */
private data class VoiceVerdict(val label: String, val confidence: Double)

/**
 * The actual WebRTC call, reached only once both sides have already passed pre-connect
 * liveness verification (VerifyScreen) -- unlike v1's CallScreen, there's no in-call liveness
 * loop here anymore; that already happened. This is purely offer/answer/ICE relay + media.
 */
@Composable
fun ConnectedCallScreen(callId: String, authToken: String?, onExit: () -> Unit) {
    val context = LocalContext.current
    var hasPermissions by remember {
        mutableStateOf(CALL_PERMISSIONS.all { ContextCompat.checkSelfPermission(context, it) == PackageManager.PERMISSION_GRANTED })
    }
    val permissionLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.RequestMultiplePermissions(),
    ) { results -> hasPermissions = results.values.all { it } }

    LaunchedEffect(Unit) {
        if (!hasPermissions) permissionLauncher.launch(CALL_PERMISSIONS)
    }

    if (!hasPermissions) {
        Box(modifier = Modifier.fillMaxSize().background(Color.Black)) {
            Column(
                modifier = Modifier.fillMaxSize().padding(28.dp),
                verticalArrangement = Arrangement.Center,
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Text(
                    "SensoCrypt needs camera and microphone access to make a video call.",
                    color = Color.White,
                    textAlign = TextAlign.Center,
                    style = MaterialTheme.typography.bodyLarge,
                )
                Spacer(Modifier.height(16.dp))
                Button(onClick = { permissionLauncher.launch(CALL_PERMISSIONS) }) { Text("Grant permissions") }
            }
        }
        return
    }

    ConnectedCallScreenContent(callId = callId, authToken = authToken, onExit = onExit)
}

@Composable
private fun ConnectedCallScreenContent(callId: String, authToken: String?, onExit: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    var endedMessage by remember { mutableStateOf<String?>(null) }
    var offerSent by remember { mutableStateOf(false) }
    var signalReady by remember { mutableStateOf(false) }
    var isMuted by remember { mutableStateOf(false) }
    // Which video is the big one -- tapping the small picture-in-picture view swaps them.
    var mainIsLocal by remember { mutableStateOf(false) }
    var voiceVerdict by remember { mutableStateOf<VoiceVerdict?>(null) }

    val eglBase = remember { EglBase.create() }
    val webRtcSession = remember { WebRtcSession(context, eglBase) }
    val signalSocket = remember { SignalSocket(callId) }

    val localRenderer = remember { SurfaceViewRenderer(context) }
    val remoteRenderer = remember { SurfaceViewRenderer(context).apply { init(eglBase.eglBaseContext, null) } }

    fun sendSignal(json: JSONObject) {
        signalSocket.send(json.toString())
    }

    fun endCall() {
        sendSignal(JSONObject().apply { put("type", "end") })
        onExit()
    }

    fun sendOfferOnce() {
        if (offerSent) return
        offerSent = true
        webRtcSession.createOffer { offer ->
            sendSignal(JSONObject().apply { put("type", "offer"); put("sdp", offer.description) })
        }
    }

    LaunchedEffect(callId) {
        webRtcSession.onIceCandidate = { candidate ->
            sendSignal(
                JSONObject().apply {
                    put("type", "ice")
                    put("candidate", candidate.sdp)
                    put("sdpMid", candidate.sdpMid)
                    put("sdpMLineIndex", candidate.sdpMLineIndex)
                },
            )
        }
        webRtcSession.onRemoteVideoTrack = { track -> track.addSink(remoteRenderer) }
        webRtcSession.onRemoteAudioTrack = { track ->
            Log.i("SensoCrypt", "voice detection: remote audio track attached, arming recorder")
            // Up to 3 tries of 5s each, at the start of the call, not continuous -- see
            // VoiceDetectionRecorder's docstring for why. A "human" result stops it
            // immediately (recorder.stop()); an "ai_generated" result just lets the
            // recorder's own maxTries limit take over, moving on to the next try -- a
            // single AI-flagged try alone isn't treated as conclusive.
            lateinit var recorder: VoiceDetectionRecorder
            recorder = VoiceDetectionRecorder(windowSeconds = 5.0, maxTries = 3) { tryNumber, wavBytes ->
                scope.launch {
                    val token = authToken ?: return@launch
                    try {
                        val result = VoiceApi().detect(wavBytes, token)
                        Log.i("SensoCrypt", "voice detection: try #$tryNumber -> ${result.label} (${result.confidence})")
                        voiceVerdict = VoiceVerdict(result.label, result.confidence)
                        if (result.label == "human") recorder.stop()
                    } catch (e: Exception) {
                        // Best-effort -- a failed try (e.g. a cold Cloud Run start, or a
                        // network blip) shouldn't interrupt the call, just leave the badge
                        // showing "Analyzing..." until the next try (if any remain). Still
                        // logged so the failure is visible in logcat instead of invisible.
                        Log.w("SensoCrypt", "voice detection: try #$tryNumber failed: ${e.message}")
                    }
                }
            }
            track.addSink(recorder)
        }

        webRtcSession.start(localRenderer)
        signalSocket.connect()

        scope.launch {
            signalSocket.messages.collect { raw ->
                val json = try { JSONObject(raw) } catch (e: Exception) { return@collect }
                when (json.optString("type")) {
                    "offer" -> {
                        webRtcSession.setRemoteDescription(SessionDescription(SessionDescription.Type.OFFER, json.getString("sdp")))
                        webRtcSession.createAnswer { answer ->
                            sendSignal(JSONObject().apply { put("type", "answer"); put("sdp", answer.description) })
                        }
                    }
                    "answer" -> {
                        webRtcSession.setRemoteDescription(SessionDescription(SessionDescription.Type.ANSWER, json.getString("sdp")))
                    }
                    "ready" -> {
                        if (json.optString("role") == "offerer") {
                            signalReady = true
                            sendOfferOnce()
                        }
                    }
                    "ice" -> {
                        webRtcSession.addIceCandidate(
                            IceCandidate(json.getString("sdpMid"), json.getInt("sdpMLineIndex"), json.getString("candidate")),
                        )
                    }
                    "end" -> endedMessage = "The other person ended the call."
                    "error" -> endedMessage = json.optString("message", "Couldn't connect the call.")
                }
            }
        }
    }

    DisposableEffect(Unit) {
        onDispose {
            webRtcSession.close()
            signalSocket.close()
            localRenderer.release()
            remoteRenderer.release()
            eglBase.release()
        }
    }

    val fullScreenModifier = Modifier.fillMaxSize()
    val pipModifier = Modifier
        .size(110.dp, 150.dp)
        .padding(bottom = 96.dp, end = 16.dp)
        .clip(RoundedCornerShape(14.dp))
        .border(1.dp, Color.White.copy(alpha = 0.25f), RoundedCornerShape(14.dp))
        .clickable { mainIsLocal = !mainIsLocal }

    Box(modifier = Modifier.fillMaxSize().background(Color.Black)) {
        // Each renderer stays in its own fixed AndroidView (a View can't be reparented
        // between two AndroidView hosts on recomposition -- factory only runs once) --
        // tapping the small one just swaps which ONE'S MODIFIER is fullscreen vs.
        // picture-in-picture, never which View lives where.
        AndroidView(
            modifier = if (mainIsLocal) Modifier.align(Alignment.BottomEnd).then(pipModifier) else fullScreenModifier,
            factory = { remoteRenderer },
        )
        AndroidView(
            modifier = if (mainIsLocal) fullScreenModifier else Modifier.align(Alignment.BottomEnd).then(pipModifier),
            factory = { localRenderer },
        )

        IconButton(
            onClick = { endCall() },
            modifier = Modifier.align(Alignment.TopStart).systemBarsPadding().padding(12.dp)
                .clip(CircleShape).background(Color.Black.copy(alpha = 0.45f)),
        ) {
            Icon(Icons.Filled.ArrowBack, contentDescription = "Back", tint = Color.White)
        }

        Column(
            modifier = Modifier.align(Alignment.TopCenter).systemBarsPadding().padding(top = 12.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            VerifiedBadge()
            Spacer(Modifier.height(8.dp))
            VoiceDetectionBadge(verdict = voiceVerdict)
        }

        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(20.dp),
            modifier = Modifier.align(Alignment.BottomCenter).systemBarsPadding().padding(bottom = 16.dp),
        ) {
            IconButton(
                onClick = {
                    isMuted = !isMuted
                    webRtcSession.setMuted(isMuted)
                },
                modifier = Modifier.size(52.dp).clip(CircleShape)
                    .background(if (isMuted) Color.White else Color.Black.copy(alpha = 0.45f)),
            ) {
                Icon(
                    if (isMuted) Icons.Filled.MicOff else Icons.Filled.Mic,
                    contentDescription = if (isMuted) "Unmute" else "Mute",
                    tint = if (isMuted) Color.Black else Color.White,
                )
            }

            IconButton(
                onClick = { endCall() },
                modifier = Modifier.size(64.dp).clip(CircleShape).background(MaterialTheme.colorScheme.error),
            ) {
                Icon(Icons.Filled.CallEnd, contentDescription = "End call", tint = Color.White, modifier = Modifier.size(28.dp))
            }

            IconButton(
                onClick = { webRtcSession.switchCamera() },
                modifier = Modifier.size(52.dp).clip(CircleShape).background(Color.Black.copy(alpha = 0.45f)),
            ) {
                Icon(Icons.Filled.Cameraswitch, contentDescription = "Switch camera", tint = Color.White)
            }
        }

        endedMessage?.let { message -> CallEndedOverlayView(message = message, onBackToHome = onExit) }
    }
}

@Composable
private fun VerifiedBadge(modifier: Modifier = Modifier) {
    androidx.compose.foundation.layout.Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = modifier
            .clip(RoundedCornerShape(14.dp))
            .background(Color(0xFF1B4332).copy(alpha = 0.9f))
            .padding(horizontal = 14.dp, vertical = 8.dp),
    ) {
        Icon(Icons.Filled.Lock, contentDescription = null, tint = Color.White, modifier = Modifier.size(16.dp))
        Spacer(Modifier.width(8.dp))
        Text("Verified — secure call", color = Color.White, style = MaterialTheme.typography.labelLarge)
    }
}

/** Live AI-vs-human badge for the other party's voice, updated roughly every ~4 seconds
 * throughout the call as VoiceDetectionRecorder finishes each window. */
@Composable
private fun VoiceDetectionBadge(verdict: VoiceVerdict?, modifier: Modifier = Modifier) {
    val (background, icon, label) = when {
        verdict == null -> Triple(Color(0xFF3A3A3A).copy(alpha = 0.9f), null, "Analyzing voice…")
        verdict.label == "human" -> Triple(Color(0xFF1B4332).copy(alpha = 0.9f), Icons.Filled.RecordVoiceOver, "Human voice")
        else -> Triple(Color(0xFF7A1F1F).copy(alpha = 0.9f), Icons.Filled.Warning, "AI voice detected")
    }
    androidx.compose.foundation.layout.Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = modifier
            .clip(RoundedCornerShape(14.dp))
            .background(background)
            .padding(horizontal = 14.dp, vertical = 8.dp),
    ) {
        if (icon != null) {
            Icon(icon, contentDescription = null, tint = Color.White, modifier = Modifier.size(16.dp))
            Spacer(Modifier.width(8.dp))
        }
        Text(label, color = Color.White, style = MaterialTheme.typography.labelLarge)
    }
}

@Composable
private fun CallEndedOverlayView(message: String, onBackToHome: () -> Unit) {
    Box(modifier = Modifier.fillMaxSize().background(Color.Black.copy(alpha = 0.85f)), contentAlignment = Alignment.Center) {
        Column(modifier = Modifier.padding(32.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Icon(Icons.Filled.CallEnd, contentDescription = null, tint = Color.White.copy(alpha = 0.8f), modifier = Modifier.size(40.dp))
            Spacer(Modifier.height(12.dp))
            Text(message, color = Color.White, style = MaterialTheme.typography.titleMedium, textAlign = TextAlign.Center)
            Spacer(Modifier.height(20.dp))
            Button(onClick = onBackToHome) { Text("Back to Home") }
        }
    }
}
