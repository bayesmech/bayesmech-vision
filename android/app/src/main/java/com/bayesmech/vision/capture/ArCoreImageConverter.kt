package com.bayesmech.vision.capture

import android.graphics.Bitmap
import android.graphics.ImageFormat
import android.graphics.Rect
import android.graphics.YuvImage
import android.media.Image
import android.util.Log
import com.google.ar.core.Frame
import java.io.ByteArrayOutputStream

object ArCoreImageConverter {
    private const val TAG = "ArCoreImageConverter"

    fun extractCameraImageBitmap(frame: Frame): Bitmap? {
        return try {
            val cameraImage = frame.acquireCameraImage()
            try {
                encodeCameraImageBitmap(cameraImage)
            } finally {
                cameraImage.close()
            }
        } catch (e: Exception) {
            Log.e(TAG, "Error extracting camera image from ARCore", e)
            null
        }
    }

    private fun encodeCameraImageBitmap(cameraImage: Image): Bitmap? {
        val width = cameraImage.width
        val height = cameraImage.height
        val nv21 = yuv420888ToNv21(cameraImage)
        val yuvImage = YuvImage(nv21, ImageFormat.NV21, width, height, null)
        val output = ByteArrayOutputStream()
        yuvImage.compressToJpeg(Rect(0, 0, width, height), 100, output)
        return android.graphics.BitmapFactory.decodeByteArray(output.toByteArray(), 0, output.size())
    }

    private fun yuv420888ToNv21(image: Image): ByteArray {
        val width = image.width
        val height = image.height
        val ySize = width * height
        val uvSize = width * height / 2
        val nv21 = ByteArray(ySize + uvSize)

        copyPlane(
            plane = image.planes[0],
            width = width,
            height = height,
            out = nv21,
            outOffset = 0,
            outPixelStride = 1
        )
        copyChromaPlanesToNv21(
            uPlane = image.planes[1],
            vPlane = image.planes[2],
            width = width / 2,
            height = height / 2,
            out = nv21,
            outOffset = ySize
        )
        return nv21
    }

    private fun copyPlane(
        plane: Image.Plane,
        width: Int,
        height: Int,
        out: ByteArray,
        outOffset: Int,
        outPixelStride: Int
    ) {
        val buffer = plane.buffer
        val rowStride = plane.rowStride
        val pixelStride = plane.pixelStride
        var outputIndex = outOffset

        for (row in 0 until height) {
            var inputIndex = row * rowStride
            repeat(width) {
                out[outputIndex] = buffer.get(inputIndex)
                outputIndex += outPixelStride
                inputIndex += pixelStride
            }
        }
    }

    private fun copyChromaPlanesToNv21(
        uPlane: Image.Plane,
        vPlane: Image.Plane,
        width: Int,
        height: Int,
        out: ByteArray,
        outOffset: Int
    ) {
        val uBuffer = uPlane.buffer
        val vBuffer = vPlane.buffer
        val uRowStride = uPlane.rowStride
        val vRowStride = vPlane.rowStride
        val uPixelStride = uPlane.pixelStride
        val vPixelStride = vPlane.pixelStride
        var outputIndex = outOffset

        for (row in 0 until height) {
            var uInputIndex = row * uRowStride
            var vInputIndex = row * vRowStride
            repeat(width) {
                out[outputIndex++] = vBuffer.get(vInputIndex)
                out[outputIndex++] = uBuffer.get(uInputIndex)
                uInputIndex += uPixelStride
                vInputIndex += vPixelStride
            }
        }
    }
}
