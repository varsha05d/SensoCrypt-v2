package com.sensocrypt.call

import android.util.Log
import org.webrtc.AudioTrackSink
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Taps the REMOTE party's decoded call audio via WebRTC's AudioTrackSink -- this is the
 * other person's voice as heard locally, which is what "is the caller AI or human"
 * detection needs to run against. Deliberately not the local mic (that's the user's own
 * voice): WebRtcSession.onRemoteAudioTrack hands us the incoming AudioTrack, and
 * addSink(this) below is how it starts feeding onData() here.
 *
 * Buffers a fixed-length window and emits it as a 16-bit PCM WAV clip via onWindowReady --
 * up to maxTries times, at the start of the call, not continuous throughout it. The caller
 * decides whether a given try was conclusive: call stop() as soon as a try comes back
 * "human" (no need to check further), and just let a "ai_generated" try fall through to
 * the next one automatically (up to maxTries) -- a single AI-flagged window is exactly the
 * kind of false positive real testing showed this model produces on real compressed call
 * audio, so requiring it to say so more than once (or reaching the try limit) is a
 * deliberate way to cut down on those false alarms.
 *
 * onData() runs on WebRTC's own audio thread -- onWindowReady must return quickly (hand
 * off to a coroutine for the actual network call) or it'll stall audio delivery.
 */
class VoiceDetectionRecorder(
    private val windowSeconds: Double = 5.0,
    private val maxTries: Int = 3,
    private val onWindowReady: (tryNumber: Int, wavBytes: ByteArray) -> Unit,
) : AudioTrackSink {
    private var buffer = ByteArrayOutputStream()
    private var sampleRate = -1
    private var bitsPerSample = -1
    private var channels = -1
    private var targetSamples = 0
    private var samplesBuffered = 0
    private var triesEmitted = 0
    @Volatile private var stopped = false

    /** Called by the caller once a try's result makes further checking pointless (a
     * "human" verdict) -- prevents any further windows from being buffered/emitted. */
    fun stop() {
        stopped = true
    }

    override fun onData(
        data: ByteBuffer,
        bitsPerSample: Int,
        sampleRate: Int,
        numberOfChannels: Int,
        numberOfFrames: Int,
        absoluteCaptureTimestampMs: Long,
    ) {
        if (stopped || triesEmitted >= maxTries) return

        if (sampleRate != this.sampleRate || bitsPerSample != this.bitsPerSample || numberOfChannels != this.channels) {
            // First call, or the remote format changed mid-call (renegotiation) -- restart
            // the window against the new format rather than mixing sample rates in one WAV.
            Log.i(
                "SensoCrypt",
                "VoiceDetectionRecorder: format ${sampleRate}Hz/${bitsPerSample}bit/${numberOfChannels}ch " +
                    "(was ${this.sampleRate}/${this.bitsPerSample}/${this.channels})",
            )
            this.sampleRate = sampleRate
            this.bitsPerSample = bitsPerSample
            this.channels = numberOfChannels
            this.targetSamples = (sampleRate * windowSeconds).toInt()
            buffer = ByteArrayOutputStream()
            samplesBuffered = 0
        }

        val chunk = ByteArray(data.remaining())
        data.get(chunk)
        buffer.write(chunk)
        samplesBuffered += numberOfFrames

        if (samplesBuffered >= targetSamples) {
            triesEmitted++
            Log.i("SensoCrypt", "VoiceDetectionRecorder: try #$triesEmitted ready, ${buffer.size()} bytes / $samplesBuffered samples")
            onWindowReady(triesEmitted, toWav(buffer.toByteArray(), sampleRate, bitsPerSample, channels))
            buffer = ByteArrayOutputStream()
            samplesBuffered = 0
        }
    }

    private fun toWav(pcm: ByteArray, sampleRate: Int, bitsPerSample: Int, channels: Int): ByteArray {
        val byteRate = sampleRate * channels * bitsPerSample / 8
        val blockAlign = channels * bitsPerSample / 8
        val header = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN).apply {
            put("RIFF".toByteArray(Charsets.US_ASCII))
            putInt(36 + pcm.size)
            put("WAVE".toByteArray(Charsets.US_ASCII))
            put("fmt ".toByteArray(Charsets.US_ASCII))
            putInt(16) // fmt chunk size for PCM
            putShort(1) // audio format: 1 = PCM
            putShort(channels.toShort())
            putInt(sampleRate)
            putInt(byteRate)
            putShort(blockAlign.toShort())
            putShort(bitsPerSample.toShort())
            put("data".toByteArray(Charsets.US_ASCII))
            putInt(pcm.size)
        }
        return header.array() + pcm
    }
}
