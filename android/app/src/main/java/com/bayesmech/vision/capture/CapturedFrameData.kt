package com.bayesmech.vision.capture

import android.graphics.Bitmap
import android.media.Image
import com.google.ar.core.PointCloud

data class CapturedFrameData(
    val rgbBitmap: Bitmap?,
    val depthImage: Image?,
    val pointCloud: PointCloud?
) {
    val rgbWidth: Int
        get() = rgbBitmap?.width ?: 0

    val rgbHeight: Int
        get() = rgbBitmap?.height ?: 0

    fun close() {
        rgbBitmap?.recycle()
        depthImage?.close()
        pointCloud?.close()
    }
}
