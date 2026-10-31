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
