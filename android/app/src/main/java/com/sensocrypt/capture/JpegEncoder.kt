package com.sensocrypt.capture

import android.graphics.Bitmap
import java.io.ByteArrayOutputStream

/** plan.md §4.5: frames go over the wire as JPEG q40, in colour when the frame has it
 * (the illumination check needs it), otherwise gray replicated into R=G=B --
 * JPEG's chroma subsampling compresses the (constant, redundant) color channels away
 * cheaply, so this costs little bandwidth over a true single-channel encoder. */
fun grayToJpeg(frame: GrayFrame, quality: Int = 60): ByteArray {
    val pixels = IntArray(frame.width * frame.height)
    val rgb = frame.rgb
    for (i in pixels.indices) {
        pixels[i] = if (rgb != null) {
            val r = rgb[i * 3].toInt() and 0xFF
            val g = rgb[i * 3 + 1].toInt() and 0xFF
            val b = rgb[i * 3 + 2].toInt() and 0xFF
            (0xFF shl 24) or (r shl 16) or (g shl 8) or b
        } else {
            val g = frame.pixels[i].toInt() and 0xFF
            (0xFF shl 24) or (g shl 16) or (g shl 8) or g
        }
    }
    val bitmap = Bitmap.createBitmap(pixels, frame.width, frame.height, Bitmap.Config.ARGB_8888)
    val out = ByteArrayOutputStream()
    bitmap.compress(Bitmap.CompressFormat.JPEG, quality, out)
    bitmap.recycle()
    return out.toByteArray()
}
