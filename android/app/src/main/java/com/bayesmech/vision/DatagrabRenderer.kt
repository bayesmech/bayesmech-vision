package com.bayesmech.vision

import android.opengl.Matrix
import android.util.Log
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.LifecycleOwner
import com.google.ar.core.TrackingState
import com.bayesmech.vision.common.helpers.DeviceTimestamp
import com.bayesmech.vision.capture.ArCoreFrameSampler
import com.bayesmech.vision.common.helpers.DisplayRotationHelper
import com.bayesmech.vision.common.samplerender.Mesh
import com.bayesmech.vision.common.samplerender.SampleRender
import com.bayesmech.vision.common.samplerender.Shader
import com.bayesmech.vision.common.samplerender.VertexBuffer
import com.bayesmech.vision.common.samplerender.arcore.BackgroundRenderer
import com.bayesmech.vision.common.samplerender.arcore.PlaneRenderer
import com.google.ar.core.exceptions.CameraNotAvailableException
import com.google.ar.core.exceptions.NotYetAvailableException
import java.io.IOException
import com.bayesmech.vision.network.ARStreamClient
import com.bayesmech.vision.network.StreamConfig
import com.bayesmech.vision.capture.ARDataCapture
import com.bayesmech.vision.sensors.SensorDataCollector
import com.bayesmech.vision.coverage.CoverageTracker
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

class DatagrabRenderer(val activity: MainActivity) :
    SampleRender.Renderer, DefaultLifecycleObserver {

    private val TAG = "DatagrabRenderer"

    private val Z_NEAR = 0.1f
    private val Z_FAR = 100f

    lateinit var planeRenderer: PlaneRenderer
    lateinit var backgroundRenderer: BackgroundRenderer
    lateinit var pointCloudVertexBuffer: VertexBuffer
    lateinit var pointCloudMesh: Mesh
    lateinit var pointCloudShader: Shader

    var hasSetTextureNames = false
    var lastPointCloudTimestamp: Long = 0

    val viewMatrix = FloatArray(16)
    val projectionMatrix = FloatArray(16)
    val modelViewProjectionMatrix = FloatArray(16)

    val session get() = activity.arCoreSessionHelper.session

    val displayRotationHelper = DisplayRotationHelper(activity)

    private var streamClient: ARStreamClient? = null
    private var dataCapture: ARDataCapture? = null
    private var sensorCollector: SensorDataCollector? = null
    private val coverageTracker = CoverageTracker()
    private val streamingScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    private var currentConfig: StreamConfig? = null
    private var deviceId: String = ""

    // Expose the coverage tracker so MainActivity can poll it
    fun getCoverageTracker(): CoverageTracker = coverageTracker

    override fun onResume(owner: LifecycleOwner) {
        displayRotationHelper.onResume()
        hasSetTextureNames = false
    }

    override fun onPause(owner: LifecycleOwner) {
        displayRotationHelper.onPause()
    }

    override fun onSurfaceCreated(render: SampleRender) {
        try {
            planeRenderer = PlaneRenderer(render)
            backgroundRenderer = BackgroundRenderer(render)

            pointCloudShader = Shader.createFromAssets(
                render,
                "shaders/anchors_point.vert",
                "shaders/anchors_point.frag",
                null
            )
                .setVec4("u_Color", floatArrayOf(31.0f / 255.0f, 188.0f / 255.0f, 210.0f / 255.0f, 1.0f))
                .setFloat("u_PointSize", 5.0f)

            pointCloudVertexBuffer = VertexBuffer(render, 4, null)
            val pointCloudVertexBuffers = arrayOf(pointCloudVertexBuffer)
            pointCloudMesh = Mesh(render, Mesh.PrimitiveMode.POINTS, null, pointCloudVertexBuffers)
        } catch (e: IOException) {
            Log.e(TAG, "Failed to read a required asset file", e)
            showError("Failed to read a required asset file: $e")
        }
    }

    override fun onSurfaceChanged(render: SampleRender, width: Int, height: Int) {
        displayRotationHelper.onSurfaceChanged(width, height)
    }

    override fun onDrawFrame(render: SampleRender) {
        val session = session ?: return

        if (!hasSetTextureNames) {
            session.setCameraTextureNames(intArrayOf(backgroundRenderer.cameraColorTexture.textureId))
            hasSetTextureNames = true
        }

        displayRotationHelper.updateSessionIfNeeded(session)

        val frame = try {
            session.update()
        } catch (e: CameraNotAvailableException) {
            Log.e(TAG, "Camera not available during onDrawFrame", e)
            showError("Camera not available. Try restarting the app.")
            return
        } catch (e: com.google.ar.core.exceptions.SessionPausedException) {
            // Session is paused during a lifecycle transition — skip frame, resume will follow
            return
        }

        val camera = frame.camera
        val vm = activity.appViewModel

        try {
            backgroundRenderer.setUseDepthVisualization(
                render,
                vm.visualizeDepthMap.value
            )
        } catch (e: IOException) {
            Log.e(TAG, "Failed to read a required asset file", e)
            showError("Failed to read a required asset file: $e")
            return
        }

        backgroundRenderer.updateDisplayGeometry(frame)

        val shouldGetDepthImage = vm.enableDepthData.value || vm.visualizeDepthMap.value
        if (camera.trackingState == TrackingState.TRACKING && shouldGetDepthImage) {
            try {
                val depthImage = frame.acquireDepthImage16Bits()
                backgroundRenderer.updateCameraDepthTexture(depthImage)
                depthImage.close()
            } catch (e: NotYetAvailableException) {
                // Depth not yet available — normal at startup
            }
        }

        backgroundRenderer.drawBackground(render)

        dataCapture?.let { capture ->
            if (camera.trackingState == TrackingState.TRACKING) {
                val streamConfig = currentConfig ?: return@let
                val capturedFrame = ArCoreFrameSampler.capture(
                    frame = frame,
                    includeRgb = streamConfig.sendRgbFrames,
                    includeDepth = vm.enableDepthData.value && streamConfig.sendDepthFrames,
                    includePointCloud = vm.enableInferredGeometry.value
                )

                streamingScope.launch {
                    try {
                        capture.captureAndSend(
                            frame = frame,
                            session = session,
                            camera = camera,
                            capturedFrame = capturedFrame
                        )
                    } finally {
                        capturedFrame.close()
                    }
                }
            }
        }

        if (camera.trackingState == TrackingState.PAUSED) return

        camera.getProjectionMatrix(projectionMatrix, 0, Z_NEAR, Z_FAR)
        camera.getViewMatrix(viewMatrix, 0)

        if (vm.visualizePointCloud.value) {
            try {
                frame.acquirePointCloud().use { pointCloud ->
                    if (pointCloud.timestamp > lastPointCloudTimestamp) {
                        pointCloudVertexBuffer.set(pointCloud.points)
                        lastPointCloudTimestamp = pointCloud.timestamp
                    }
                    Matrix.multiplyMM(modelViewProjectionMatrix, 0, projectionMatrix, 0, viewMatrix, 0)
                    pointCloudShader.setMat4("u_ModelViewProjection", modelViewProjectionMatrix)
                    render.draw(pointCloudMesh, pointCloudShader)
                }
            } catch (e: Exception) {
                Log.w(TAG, "Could not acquire point cloud for rendering: ${e.message}")
            }
        }

        if (vm.visualizePlanes.value) {
            planeRenderer.drawPlanes(
                render,
                session.getAllTrackables(com.google.ar.core.Plane::class.java),
                camera.displayOrientedPose,
                projectionMatrix
            )
        }
    }

    fun startStreaming(config: StreamConfig) {
        currentConfig = config
        streamClient = ARStreamClient(config.serverUrl)

        streamClient?.setStatusCallback { status ->
            activity.runOnUiThread {
                activity.appViewModel.updateConnectionStatus(status)
            }
        }
        streamClient?.connect()

        deviceId = android.provider.Settings.Secure.getString(
            activity.contentResolver,
            android.provider.Settings.Secure.ANDROID_ID
        ) ?: ""

        sensorCollector = SensorDataCollector(activity)
        sensorCollector?.startCollecting()

        dataCapture = ARDataCapture(
            client = streamClient!!,
            config = config,
            deviceId = deviceId,
            sensorCollector = sensorCollector!!,
            recordingManager = activity.recordingManager,
            coverageTracker = coverageTracker,
            getEnableDepth = { activity.appViewModel.enableDepthData.value },
            getEnableGeometry = { activity.appViewModel.enableInferredGeometry.value }
        )

        Log.i(TAG, "AR streaming started to ${config.serverUrl} with device_id: $deviceId")
    }

    fun stopStreaming() {
        sensorCollector?.stopCollecting()
        sensorCollector = null
        streamClient?.disconnect()
        streamClient = null
        dataCapture = null
        Log.i(TAG, "AR streaming stopped")
    }

    fun restartStreaming(newUrl: String) {
        val config = currentConfig ?: return
        stopStreaming()
        startStreaming(config.copy(serverUrl = newUrl))
    }

    fun isStreaming(): Boolean = streamClient != null

    fun sendUserTextInput(text: String) {
        val client = streamClient ?: return
        val frameTimestampNs = DeviceTimestamp.nowNs()
        val frame = com.bayesmech.vision.PerceiverDataFrame.newBuilder()
            .setFrameIdentifier(
                com.bayesmech.vision.PerceiverFrameIdentifier.newBuilder()
                    .setTimestampNs(frameTimestampNs)
                    .setDeviceId(deviceId)
            )
            .setUserTextInput(text)
            .build()
        client.sendFrame(frame)
    }

    private fun showError(errorMessage: String) {
        activity.appViewModel.setArcoreError(errorMessage)
    }
}
