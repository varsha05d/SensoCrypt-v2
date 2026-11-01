package com.sensocrypt.call

/**
 * Plain-language wording for HOW a person was verified. The backend names the check that passed
 * first ("gyro", "accelerometer" or "illumination"); users see "movement" or "flash".
 * Display only -- the verification itself is decided entirely on the server.
 */
fun verifiedViaWord(via: String?): String? = when (via) {
    "gyro", "accelerometer" -> "movement"
    "illumination" -> "flash"
    else -> null
}

/** "Verified via movement" / "Verified via flash" (or just "Verified" if the method is unknown). */
fun verifiedViaMessage(via: String?): String =
    verifiedViaWord(via)?.let { "Verified via $it" } ?: "Verified"

/**
 * Parses the backend's per-side summary, e.g. "caller=verified_gyro,callee=verified_illumination",
 * into side -> how that side was verified ("gyro", "accelerometer", "illumination"); a side that did
 * not verify, or an older summary without a method, is simply absent.
 */
fun verifiedViaBySide(sides: String?): Map<String, String> =
    sides?.split(",")?.mapNotNull { part ->
        val (side, status) = part.split("=", limit = 2).let { it[0] to it.getOrNull(1) }
        if (status != null && status.startsWith("verified_")) side to status.removePrefix("verified_") else null
    }?.toMap() ?: emptyMap()

/** The side opposite to [mySide] ("caller" <-> "callee"). */
fun otherSide(mySide: String): String = if (mySide == "caller") "callee" else "caller"
