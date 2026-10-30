package com.sensocrypt.capture

import android.graphics.ImageFormat
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import java.nio.ByteBuffer

/**
 * A downscaled frame, timestamped on the device (sensor) clock via Synchronizer. [pixels] is
 * the luma plane (what optical flow uses); [rgb] is the same frame in colour (interleaved
 * R,G,B), which the backend's illumination check needs to tell a lit face (skin absorbs
 * blue) from a glossy screen mirroring the flash (neutral response across channels).
 */
class GrayFrame(
    val timestampNs: Long,
    val width: Int,
    val height: Int,
    val pixels: ByteArray,
    val rgb: ByteArray? = null,
)

private const val TARGET_W = 160
private const val TARGET_H = 120
private const val TARGET_FPS = 15
private const val MIN_FRAME_INTERVAL_NS = 1_000_000_000L / TARGET_FPS

/**
 * CameraX ImageAnalysis.Analyzer producing the 160x120 grayscale stream plan.md §0.2 calls
 * for: the verifier needs its own copy of what the camera saw, downscaled to keep bandwidth
 * low once this feeds the telemetry channel (Phase 4). Luma-plane-only (Y of YUV_420_888) --
 * no color needed for optical flow.
 */
class FrameCapture(
    private val synchronizer: Synchronizer,
    private val onFrame: (GrayFrame) -> Unit,
) : ImageAnalysis.Analyzer {
    private var lastEmittedNs = 0L

    override fun analyze(image: ImageProxy) {
        try {
            if (image.format != ImageFormat.YUV_420_888) return
            val rawTs = image.imageInfo.timestamp
            synchronizer.observe(rawTs)

            if (rawTs - lastEmittedNs < MIN_FRAME_INTERVAL_NS) return
            lastEmittedNs = rawTs

            val yPlane = image.planes[0]
            val gray = downscaleLuma(yPlane.buffer, yPlane.rowStride, image.width, image.height, TARGET_W, TARGET_H)
            val rgb = if (image.planes.size >= 3) downscaleRgb(image) else null
            val deviceTs = if (synchronizer.isCalibrated) synchronizer.toDeviceClock(rawTs) else rawTs
            onFrame(GrayFrame(deviceTs, TARGET_W, TARGET_H, gray, rgb))
        } finally {
            image.close()
        }
    }

    private fun downscaleLuma(
        buffer: ByteBuffer,
        rowStride: Int,
        srcW: Int,
        srcH: Int,
        dstW: Int,
        dstH: Int,
    ): ByteArray {
        val bytes = ByteArray(buffer.remaining())
        buffer.get(bytes)
        val out = ByteArray(dstW * dstH)
        for (dy in 0 until dstH) {
            val sy = dy * srcH / dstH
            for (dx in 0 until dstW) {
                val sx = dx * srcW / dstW
                out[dy * dstW + dx] = bytes[sy * rowStride + sx]
            }
        }
        return out
    }

    /** YUV_420_888 -> interleaved RGB at TARGET_W x TARGET_H (BT.601), sampling chroma at the
     * matching half-resolution position. Reads the Y/U/V planes by their own strides. */
    private fun downscaleRgb(image: ImageProxy): ByteArray {
        val yP = image.planes[0]
        val uP = image.planes[1]
        val vP = image.planes[2]
        val yBuf = ByteArray(yP.buffer.remaining()).also { yP.buffer.get(it) }
        val uBuf = ByteArray(uP.buffer.remaining()).also { uP.buffer.get(it) }
        val vBuf = ByteArray(vP.buffer.remaining()).also { vP.buffer.get(it) }
        val out = ByteArray(TARGET_W * TARGET_H * 3)
        for (dy in 0 until TARGET_H) {
            val sy = dy * image.height / TARGET_H
            for (dx in 0 until TARGET_W) {
                val sx = dx * image.width / TARGET_W
                val y = (yBuf[sy * yP.rowStride + sx].toInt() and 0xFF).toFloat()
                val cx = sx / 2
                val cy = sy / 2
                val u = (uBuf[cy * uP.rowStride + cx * uP.pixelStride].toInt() and 0xFF) - 128f
                val v = (vBuf[cy * vP.rowStride + cx * vP.pixelStride].toInt() and 0xFF) - 128f
                val o = (dy * TARGET_W + dx) * 3
                out[o] = (y + 1.402f * v).coerceIn(0f, 255f).toInt().toByte()
                out[o + 1] = (y - 0.344f * u - 0.714f * v).coerceIn(0f, 255f).toInt().toByte()
                out[o + 2] = (y + 1.772f * u).coerceIn(0f, 255f).toInt().toByte()
            }
        }
        return out
    }
}
