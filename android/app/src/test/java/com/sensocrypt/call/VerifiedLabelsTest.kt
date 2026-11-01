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

class VerifiedBySideTest {
    @Test fun readsHowEachSideWasVerified() {
        val m = verifiedViaBySide("caller=verified_gyro,callee=verified_illumination")
        assertEquals("gyro", m["caller"])
        assertEquals("illumination", m["callee"])
        assertEquals("movement", verifiedViaWord(m["caller"]))
        assertEquals("flash", verifiedViaWord(m["callee"]))
    }

    @Test fun aSideThatDidNotVerifyOrHasNoMethodIsAbsent() {
        assertEquals(mapOf("caller" to "accelerometer"), verifiedViaBySide("caller=verified_accelerometer,callee=no_evidence"))
        assertEquals(emptyMap<String, String>(), verifiedViaBySide("caller=verified,callee=verified"))   // older entries
        assertEquals(emptyMap<String, String>(), verifiedViaBySide(null))
    }

    @Test fun otherSideIsTheOppositeOne() {
        assertEquals("callee", otherSide("caller"))
        assertEquals("caller", otherSide("callee"))
    }
}
