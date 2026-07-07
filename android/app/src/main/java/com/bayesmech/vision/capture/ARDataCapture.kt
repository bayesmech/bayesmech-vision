package com.bayesmech.vision.capture

import android.util.Log
import com.bayesmech.vision.PerceiverDataFrame
import com.bayesmech.vision.PerceiverFrameIdentifier
import com.bayesmech.vision.common.helpers.DeviceTimestamp
import com.google.ar.core.Camera
import com.google.ar.core.Frame
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
        capturedFrame: CapturedFrameData
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
            val sensorSnapshot = sensorCollector.getCurrentSnapshot()

            val perceiverFrame = buildPerceiverDataFrame(
                frame = frame,
                session = session,
                camera = camera,
                capturedFrame = capturedFrame,
                sensorSnapshot = sensorSnapshot,
                enableDepth = enableDepth,
                enableGeometry = enableGeometry,
                frameNum = capturedFrameNumber
            )

            val isRecording = recordingManager.isRecording()
            if (isRecording) {
                recordingManager.writeFrame(perceiverFrame)
                client.sendFrame(perceiverFrame)
            }

            val frameSize = perceiverFrame.serializedSize
            bandwidthMonitor.recordSent(frameSize)

            // Record coverage for this frame
            val imuData = sensorSnapshot.toImuData()
            coverageTracker.recordFrame(
                hasDepth = enableDepth && capturedFrame.depthImage != null && perceiverFrame.hasDepthFrame(),
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
                Log.i(TAG, "  Sensors: ${sensorSnapshot.summary()}")
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
        capturedFrame: CapturedFrameData,
        sensorSnapshot: com.bayesmech.vision.sensors.SensorSnapshot,
        enableDepth: Boolean,
        enableGeometry: Boolean,
        frameNum: Int
    ): PerceiverDataFrame {
        val builder = PerceiverDataFrame.newBuilder()
        val frameTimestampNs = DeviceTimestamp.forFrame(frame)
        val encodedRgb = if (currentQuality.sendRgb && config.sendRgbFrames && capturedFrame.rgbBitmap != null) {
            CameraDataExtractor.extractRgbFrame(
                capturedFrame.rgbBitmap,
                currentQuality.jpegQuality
            )
        } else {
            null
        }
        val encodedDepth = if (enableDepth && config.sendDepthFrames && currentQuality.sendDepth && capturedFrame.depthImage != null) {
            CameraDataExtractor.processDepthImage(
                capturedFrame.depthImage,
                currentQuality.depthScale.toInt()
            )
        } else {
            null
        }
        val recordedRgbWidth = encodedRgb?.width ?: fallbackRgbWidth(camera)
        val recordedRgbHeight = encodedRgb?.height ?: fallbackRgbHeight(camera)
        val recordedDepthWidth = encodedDepth?.width ?: 0
        val recordedDepthHeight = encodedDepth?.height ?: 0

        builder.frameIdentifier = PerceiverFrameIdentifier.newBuilder()
            .setTimestampNs(frameTimestampNs)
            .setFrameNumber(frameNum)
            .setDeviceId(deviceId)
            .build()

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

        val imuData = sensorSnapshot.toImuData()
        if (imuData.hasAngularVelocity() || imuData.hasLinearAcceleration() ||
            imuData.hasGravity() || imuData.hasMagneticField()) {
            builder.imuData = imuData
        }

        if (enableGeometry) {
            builder.inferredGeometry = CameraDataExtractor.extractInferredGeometry(session, capturedFrame.pointCloud)
        }

        val gpsLocation = sensorSnapshot.gpsLocation
        if (gpsLocation != null) {
            builder.gpsLocation = gpsLocation
        }

        return builder.build()
    }

    private fun fallbackRgbWidth(camera: Camera): Int {
        val dims = camera.imageIntrinsics.imageDimensions
        return dims.getOrNull(0)?.takeIf { it > 0 } ?: 0
    }

    private fun fallbackRgbHeight(camera: Camera): Int {
        val dims = camera.imageIntrinsics.imageDimensions
        return dims.getOrNull(1)?.takeIf { it > 0 } ?: 0
    }
}
