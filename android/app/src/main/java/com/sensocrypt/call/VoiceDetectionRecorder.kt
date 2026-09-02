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
 * Buffers a single fixed-length window and emits it as a 16-bit PCM WAV clip via
 * onWindowReady -- ONE check at the start of the call, not continuous. (An earlier version
 * of this ran repeatedly throughout the call; the model's accuracy on real compressed call
 * audio wasn't reliable enough for a continuously-flickering badge to be useful -- one
 * check on the first few seconds is a deliberate, simpler scope, at the cost of not
 * noticing if the voice on the line changes partway through the call.) onData() runs on
 * WebRTC's own audio thread -- onWindowReady must return quickly (hand off to a coroutine
 * for the actual network call) or it'll stall audio delivery.
 */
class VoiceDetectionRecorder(
    private val windowSeconds: Double = 5.0,
    private val onWindowReady: (ByteArray) -> Unit,
) : AudioTrackSink {
    private var buffer = ByteArrayOutputStream()
    private var sampleRate = -1
    private var bitsPerSample = -1
    private var channels = -1
    private var targetSamples = 0
    private var samplesBuffered = 0
    private var hasEmitted = false

    override fun onData(
        data: ByteBuffer,
        bitsPerSample: Int,
        sampleRate: Int,
        numberOfChannels: Int,
        numberOfFrames: Int,
        absoluteCaptureTimestampMs: Long,
    ) {
        if (hasEmitted) return

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
            hasEmitted = true
            Log.i("SensoCrypt", "VoiceDetectionRecorder: window ready, ${buffer.size()} bytes / $samplesBuffered samples")
            onWindowReady(toWav(buffer.toByteArray(), sampleRate, bitsPerSample, channels))
            buffer = ByteArrayOutputStream() // release the buffer now that it's no longer needed
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
