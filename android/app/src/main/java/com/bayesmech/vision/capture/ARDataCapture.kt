package com.bayesmech.vision.capture

import android.graphics.Bitmap
import android.media.Image
import android.util.Log
import com.bayesmech.vision.PerceiverDataFrame
import com.bayesmech.vision.PerceiverFrameIdentifier
import com.bayesmech.vision.common.helpers.DeviceTimestamp
import com.google.ar.core.Camera
import com.google.ar.core.Frame
import com.google.ar.core.PointCloud
import com.bayesmech.vision.network.ARStreamClient
import com.bayesmech.vision.network.BandwidthMonitor
import com.bayesmech.vision.network.QualityLevel
import com.bayesmech.vision.network.StreamConfig
import com.bayesmech.vision.recording.RecordingManager
import com.bayesmech.vision.coverage.CoverageTracker
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import com.bayesmech.vision.sensors.SensorDataCollector

class ARDataCapture(
    private val client: ARStreamClient,
    private val config: StreamConfig,
    private val deviceId: String,
    private val sensorCollector: SensorDataCollector,
    private val recordingManager: RecordingManager,
    private val coverageTracker: CoverageTracker,
    private val getEnableDepth: () -> Boolean,
    private val getEnableGeometry: () -> Boolean
) {
    private val TAG = "ARDataCapture"
    private var frameNumber = 0
    private var lastSentTimestamp = 0L
    private val frameMutex = Mutex()
    private val bandwidthMonitor = BandwidthMonitor()
    private var currentQuality = QualityLevel.HIGH

    suspend fun captureAndSend(
        frame: Frame,
        session: com.google.ar.core.Session,
        camera: Camera,
        cameraFrameBitmap: Bitmap?,
        depthImage: Image?,
        pointCloudData: PointCloud?,
        imageWidth: Int,
        imageHeight: Int
    ) = withContext(Dispatchers.IO) {
        try {
            // Mutex protects the throttle check + frameNumber increment so concurrent
            // coroutines from onDrawFrame() cannot both pass the interval check and
            // produce duplicate frame numbers.
            val capturedFrameNumber: Int
            frameMutex.withLock {
                val now = System.nanoTime()
                val minInterval = (1_000_000_000 / currentQuality.targetFps).toLong()
                if (now - lastSentTimestamp < minInterval) {
                    return@withContext
                }
                lastSentTimestamp = now
                capturedFrameNumber = frameNumber
                frameNumber++
            }

            if (config.enableAdaptiveQuality) {
                currentQuality = bandwidthMonitor.getQualityLevel()
            }

            val enableDepth = getEnableDepth()
            val enableGeometry = getEnableGeometry()

            val perceiverFrame = buildPerceiverDataFrame(
                frame, session, camera, cameraFrameBitmap, depthImage, pointCloudData,
                imageWidth, imageHeight, enableDepth, enableGeometry,
                capturedFrameNumber
            )

            if (recordingManager.isRecording()) {
                recordingManager.writeFrame(perceiverFrame)
            }

            client.sendFrame(perceiverFrame)

            val frameSize = perceiverFrame.serializedSize
            bandwidthMonitor.recordSent(frameSize)

            // Record coverage for this frame
            val imuData = sensorCollector.getCurrentImuData()
            coverageTracker.recordFrame(
                hasDepth = enableDepth && depthImage != null && perceiverFrame.hasDepthFrame(),
                hasAccelerometer = imuData.hasLinearAcceleration(),
                hasGyroscope = imuData.hasAngularVelocity(),
                hasMagnetometer = imuData.hasMagneticField(),
                hasIntrinsics = perceiverFrame.hasCameraIntrinsics(),
                hasPose = perceiverFrame.hasCameraPose(),
                hasGeometry = enableGeometry && perceiverFrame.hasInferredGeometry()
                    && (perceiverFrame.inferredGeometry.planesList.isNotEmpty()
                        || perceiverFrame.inferredGeometry.pointCloudCount > 0),
                hasGps = perceiverFrame.hasGpsLocation()
            )

            if (capturedFrameNumber % 30 == 0) {
                Log.i(TAG, "Sent frame $capturedFrameNumber, quality: $currentQuality, " +
                        "bandwidth: ${"%.2f".format(bandwidthMonitor.getCurrentBandwidthMbps())} Mbps")
                Log.i(TAG, "  Sensors: ${sensorCollector.getSensorSummary()}")
                Log.i(TAG, "  Coverage: ${coverageTracker.getStats()}")
            }

        } catch (e: Exception) {
            Log.e(TAG, "Error capturing and sending frame", e)
        }
    }

    private fun buildPerceiverDataFrame(
        frame: Frame,
        session: com.google.ar.core.Session,
        camera: Camera,
        cameraFrameBitmap: Bitmap?,
        depthImage: Image?,
        pointCloudData: PointCloud?,
        imageWidth: Int,
        imageHeight: Int,
        enableDepth: Boolean,
        enableGeometry: Boolean,
        frameNum: Int
    ): PerceiverDataFrame {
        val builder = PerceiverDataFrame.newBuilder()
        val deviceTimestampNs = DeviceTimestamp.forFrame(frame)
        val encodedRgb = if (currentQuality.sendRgb && config.sendRgbFrames && cameraFrameBitmap != null) {
            CameraDataExtractor.extractRgbFrame(
                cameraFrameBitmap,
                currentQuality.jpegQuality
            )
        } else {
            null
        }
        val encodedDepth = if (enableDepth && currentQuality.sendDepth && depthImage != null) {
            CameraDataExtractor.processDepthImage(
                depthImage,
                currentQuality.depthScale.toInt()
            )
        } else {
            null
        }
        val recordedRgbWidth = encodedRgb?.width ?: imageWidth
        val recordedRgbHeight = encodedRgb?.height ?: imageHeight
        val recordedDepthWidth = encodedDepth?.width ?: 0
        val recordedDepthHeight = encodedDepth?.height ?: 0

        builder.frameIdentifier = PerceiverFrameIdentifier.newBuilder()
            .setTimestampNs(deviceTimestampNs)
            .setFrameNumber(frameNum)
            .setDeviceId(deviceId)
            .build()
        builder.deviceTimestampNs = deviceTimestampNs

        builder.cameraPose = CameraDataExtractor.extractCameraPose(camera)
        builder.cameraIntrinsics = CameraDataExtractor.extractCameraIntrinsics(
            camera,
            recordedRgbWidth,
            recordedRgbHeight,
            recordedDepthWidth,
            recordedDepthHeight
        )

        if (encodedRgb != null) {
            builder.rgbFrame = encodedRgb.frame
        }

        if (encodedDepth != null) {
            builder.depthFrame = encodedDepth.frame
        }

        val imuData = sensorCollector.getCurrentImuData()
        if (imuData.hasAngularVelocity() || imuData.hasLinearAcceleration() ||
            imuData.hasGravity() || imuData.hasMagneticField()) {
            builder.imuData = imuData
        }

        if (enableGeometry) {
            builder.inferredGeometry = CameraDataExtractor.extractInferredGeometry(session, pointCloudData)
        }

        val gpsLocation = sensorCollector.getCurrentGpsLocation()
        if (gpsLocation != null) {
            builder.gpsLocation = gpsLocation
        }

        return builder.build()
    }

    fun getCoverageStats() = coverageTracker.getStats()
}
