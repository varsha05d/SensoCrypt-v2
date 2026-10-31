package com.sensocrypt.call

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class VerifiedLabelsTest {
    @Test fun gyroAndAccelerometerBothReadAsMovement() {
        assertEquals("movement", verifiedViaWord("gyro"))
        assertEquals("movement", verifiedViaWord("accelerometer"))
        assertEquals("Verified via movement", verifiedViaMessage("gyro"))
        assertEquals("Verified via movement", verifiedViaMessage("accelerometer"))
    }

    @Test fun illuminationReadsAsFlash() {
        assertEquals("flash", verifiedViaWord("illumination"))
        assertEquals("Verified via flash", verifiedViaMessage("illumination"))
    }

    @Test fun unknownMethodFallsBackToPlainVerified() {
        assertNull(verifiedViaWord(null))
        assertNull(verifiedViaWord("something_new"))
        assertEquals("Verified", verifiedViaMessage(null))
    }
}
