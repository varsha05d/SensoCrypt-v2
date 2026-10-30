package com.sensocrypt.capture

import android.graphics.ImageFormat
import android.graphics.Matrix
import android.graphics.Rect
import android.media.Image
import androidx.camera.core.ImageInfo
import androidx.camera.core.ImageProxy
import androidx.camera.core.impl.TagBundle
import androidx.camera.core.impl.utils.ExifData
import java.nio.ByteBuffer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * FrameCapture runs on the camera thread of every verification: an exception there kills the
 * whole app (this is how the first colour-frame build crashed the moment verification
 * started). These feed it fake YUV_420_888 frames with the buffer layouts real cameras use.
 */
class FrameCaptureTest {

    private class FakePlane(private val buf: ByteBuffer, private val rowS: Int, private val pixelS: Int) :
        ImageProxy.PlaneProxy {
        override fun getRowStride() = rowS
        override fun getPixelStride() = pixelS
        override fun getBuffer(): ByteBuffer = buf
    }

    private class FakeImage(
        private val w: Int,
        private val h: Int,
        private val planeList: Array<ImageProxy.PlaneProxy>,
        private val ts: Long,
    ) : ImageProxy {
        var closed = false
        override fun close() { closed = true }
        override fun getCropRect() = Rect(0, 0, w, h)
        override fun setCropRect(rect: Rect?) = Unit
        override fun getFormat() = ImageFormat.YUV_420_888
        override fun getHeight() = h
        override fun getWidth() = w
        override fun getPlanes() = planeList
        override fun getImageInfo(): ImageInfo = object : ImageInfo {
            override fun getTagBundle(): TagBundle = TagBundle.emptyBundle()
            override fun getTimestamp() = ts
            override fun getRotationDegrees() = 0
            override fun getSensorToBufferTransformMatrix() = Matrix()
            override fun populateExifData(exifBuilder: ExifData.Builder) = Unit
        }
        @androidx.camera.core.ExperimentalGetImage
        override fun getImage(): Image? = null
    }

    /** Solid-colour frame: Y/U/V for the given RGB (BT.601), laid out like a real camera. */
    private fun solidFrame(w: Int, h: Int, r: Int, g: Int, b: Int, interleavedChroma: Boolean): FakeImage {
        val y = (0.299 * r + 0.587 * g + 0.114 * b).toInt().coerceIn(0, 255)
        val u = (128 + (-0.169 * r - 0.331 * g + 0.5 * b)).toInt().coerceIn(0, 255)
        val v = (128 + (0.5 * r - 0.419 * g - 0.081 * b)).toInt().coerceIn(0, 255)
        val yBuf = ByteBuffer.allocate(w * h).also { bb -> repeat(w * h) { bb.put(y.toByte()) }; bb.rewind() }
        val planes: Array<ImageProxy.PlaneProxy> = if (interleavedChroma) {
            // Typical Android layout: U and V planes are views of one interleaved VU buffer,
            // pixelStride 2, rowStride = width.
            val cw = w / 2
            val ch = h / 2
            val vu = ByteArray(w * ch)
            for (row in 0 until ch) for (col in 0 until cw) {
                vu[row * w + col * 2] = v.toByte()
                vu[row * w + col * 2 + 1] = u.toByte()
            }
            val vBuf = ByteBuffer.wrap(vu)
            val uBuf = ByteBuffer.wrap(vu, 1, vu.size - 1).slice()
            arrayOf(FakePlane(yBuf, w, 1), FakePlane(uBuf, w, 2), FakePlane(vBuf, w, 2))
        } else {
            val uBuf = ByteBuffer.allocate(w * h / 4).also { bb -> repeat(w * h / 4) { bb.put(u.toByte()) }; bb.rewind() }
            val vBuf = ByteBuffer.allocate(w * h / 4).also { bb -> repeat(w * h / 4) { bb.put(v.toByte()) }; bb.rewind() }
            arrayOf(FakePlane(yBuf, w, 1), FakePlane(uBuf, w / 2, 1), FakePlane(vBuf, w / 2, 1))
        }
        return FakeImage(w, h, planes, 5_000_000_000L)
    }

    private fun capture(image: FakeImage): GrayFrame {
        var out: GrayFrame? = null
        FrameCapture(Synchronizer(), { out = it }).analyze(image)
        assertTrue("analyze must always close the image", image.closed)
        return assertNotNull(out).let { out!! }
    }

    private fun px(f: GrayFrame, x: Int, y: Int): IntArray {
        val rgb = f.rgb!!
        val o = (y * f.width + x) * 3
        return intArrayOf(rgb[o].toInt() and 0xFF, rgb[o + 1].toInt() and 0xFF, rgb[o + 2].toInt() and 0xFF)
    }

    private fun assertNear(expected: Int, actual: Int, tol: Int = 6) =
        assertTrue("expected ~$expected got $actual", Math.abs(expected - actual) <= tol)

    @Test
    fun colourFramesAreProducedForInterleavedChroma_theLayoutRealPhonesUse() {
        for ((r, g, b) in listOf(Triple(200, 40, 40), Triple(40, 200, 40), Triple(40, 40, 200), Triple(220, 220, 220))) {
            val f = capture(solidFrame(640, 480, r, g, b, interleavedChroma = true))
            assertEquals(160, f.width); assertEquals(120, f.height)
            assertNotNull("rgb must be present", f.rgb)
            assertEquals(160 * 120 * 3, f.rgb!!.size)
            val p = px(f, 80, 60)
            assertNear(r, p[0]); assertNear(g, p[1]); assertNear(b, p[2])
        }
    }

    @Test
    fun colourFramesAreProducedForSeparateChromaPlanes() {
        val f = capture(solidFrame(640, 480, 200, 40, 40, interleavedChroma = false))
        val p = px(f, 10, 10)
        assertNear(200, p[0]); assertNear(40, p[1]); assertNear(40, p[2])
    }

    @Test
    fun grayPlaneIsStillProduced() {
        val f = capture(solidFrame(640, 480, 120, 120, 120, interleavedChroma = true))
        assertEquals(160 * 120, f.pixels.size)
        assertNear(120, f.pixels[0].toInt() and 0xFF)
    }

    @Test
    fun smallFrameSizesDoNotThrow() {
        for ((w, h) in listOf(320 to 240, 160 to 120, 1280 to 720)) {
            val f = capture(solidFrame(w, h, 90, 140, 200, interleavedChroma = true))
            assertNotNull(f.rgb)
        }
    }

    @Test
    fun aBrokenChromaPlaneFallsBackToGrayInsteadOfThrowing() {
        val good = solidFrame(640, 480, 100, 100, 100, interleavedChroma = true)
        val tiny = ByteBuffer.allocate(4)
        val broken = FakeImage(640, 480, arrayOf(good.planes[0], FakePlane(tiny, 640, 2), FakePlane(tiny, 640, 2)), 5_000_000_000L)
        val f = capture(broken)
        assertEquals(160 * 120, f.pixels.size)
        assertTrue("falls back to gray-only", f.rgb == null)
    }
}
