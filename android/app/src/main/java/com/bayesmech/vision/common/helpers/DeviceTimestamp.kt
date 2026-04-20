package com.bayesmech.vision.common.helpers

import android.os.SystemClock
import com.google.ar.core.Frame

object DeviceTimestamp {
    fun nowNs(): Long = SystemClock.elapsedRealtimeNanos()

    fun forFrame(frame: Frame): Long {
        val frameTimestampNs = frame.timestamp
        return if (frameTimestampNs > 0L) frameTimestampNs else nowNs()
    }
}
