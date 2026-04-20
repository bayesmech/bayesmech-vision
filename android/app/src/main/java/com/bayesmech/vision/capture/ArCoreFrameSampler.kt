package com.bayesmech.vision.capture

import com.google.ar.core.Frame

object ArCoreFrameSampler {
    fun capture(
        frame: Frame,
        includeRgb: Boolean,
        includeDepth: Boolean,
        includePointCloud: Boolean
    ): CapturedFrameData {
        val rgbBitmap = if (includeRgb) ArCoreImageConverter.extractCameraImageBitmap(frame) else null
        val depthImage = if (includeDepth) {
            try {
                frame.acquireDepthImage16Bits()
            } catch (_: Exception) {
                null
            }
        } else {
            null
        }
        val pointCloud = if (includePointCloud) {
            try {
                frame.acquirePointCloud()
            } catch (_: Exception) {
                null
            }
        } else {
            null
        }

        return CapturedFrameData(
            rgbBitmap = rgbBitmap,
            depthImage = depthImage,
            pointCloud = pointCloud
        )
    }
}
