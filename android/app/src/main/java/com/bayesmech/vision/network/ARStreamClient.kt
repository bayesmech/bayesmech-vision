package com.bayesmech.vision.network

import android.os.Handler
import android.os.Looper
import android.util.Log
import com.bayesmech.vision.PerceiverDataFrame
import org.json.JSONObject
import okhttp3.*
import okio.ByteString
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.TimeUnit

data class ConnectionStatus(
    val isConnected: Boolean,
    val lastError: String? = null,
    val lastErrorTime: Long? = null,
    val firstFailureTime: Long? = null,
    val connectionAttempts: Int = 0,
    val lastSuccessfulConnection: Long? = null,
    val serverUrl: String = "",
    val responseCode: Int? = null,
    val networkError: String? = null,
    val isRetrying: Boolean = false,
    val retryCount: Int = 0,
    val nextRetryInMs: Long? = null
)

class ARStreamClient(
    private val serverUrl: String
) {
    private val TAG = "ARStreamClient"
    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .pingInterval(20, TimeUnit.SECONDS)
        .build()
    
    private var webSocket: WebSocket? = null
    private val isConnected = AtomicBoolean(false)

    // Connection status tracking
    @Volatile
    private var connectionStatus = ConnectionStatus(
        isConnected = false,
        serverUrl = serverUrl
    )
    
    private var connectionAttempts = 0
    private var statusCallback: ((ConnectionStatus) -> Unit)? = null
    private var firstFailureTime: Long? = null

    // Automatic reconnection
    private val reconnectHandler = Handler(Looper.getMainLooper())
    private var retryCount = 0
    private var autoReconnectEnabled = true
    private var isReconnecting = false

    @Volatile
    private var activeRecordingName: String? = null
    
    private fun buildStreamUrl(): String = "${serverUrl.trim().trimEnd('/')}/ar-stream"

    // Retry delays in milliseconds (exponential backoff with cap)
    private fun getRetryDelay(retryCount: Int): Long {
        val baseDelay = 500L  // Start with 500ms
        val maxDelay = 5000L  // Cap at 5 seconds
        val delay = (baseDelay * Math.pow(1.5, retryCount.toDouble())).toLong()
        return delay.coerceAtMost(maxDelay)
    }

    // Metrics
    private var bytesSent = 0L
    private var framesSent = 0
    private var framesDropped = 0

    fun setStatusCallback(callback: (ConnectionStatus) -> Unit) {
        statusCallback = callback
        // Immediately notify with current status
        statusCallback?.invoke(connectionStatus)
    }

    private fun updateStatus(status: ConnectionStatus) {
        connectionStatus = status
        statusCallback?.invoke(status)
    }

    fun connect() {
        connectionAttempts++
        Log.i(TAG, "Attempting to connect to $serverUrl (attempt #$connectionAttempts)")

        val request = try {
            Request.Builder().url(buildStreamUrl()).build()
        } catch (e: IllegalArgumentException) {
            Log.w(TAG, "Invalid server URL '$serverUrl': ${e.message}")
            updateStatus(ConnectionStatus(isConnected = false, serverUrl = serverUrl, lastError = "Invalid URL: ${e.message}"))
            return
        }

        try {
            webSocket = client.newWebSocket(request, object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    isConnected.set(true)
                    isReconnecting = false
                    retryCount = 0
                    firstFailureTime = null

                    Log.i(TAG, "✓ WebSocket connected to $serverUrl")
                    Log.i(TAG, "  Response code: ${response.code}")
                    Log.i(TAG, "  Protocol: ${response.protocol}")
                    
                    // Cancel any pending reconnect attempts
                    reconnectHandler.removeCallbacksAndMessages(null)
                    
                    updateStatus(ConnectionStatus(
                        isConnected = true,
                        connectionAttempts = connectionAttempts,
                        lastSuccessfulConnection = System.currentTimeMillis(),
                        serverUrl = serverUrl,
                        responseCode = response.code,
                        isRetrying = false,
                        retryCount = 0
                    ))

                    activeRecordingName?.let { sendControl("start_recording", it) }
                }

                override fun onMessage(webSocket: WebSocket, text: String) {
                    Log.d(TAG, "← Received text message: $text")
                }

                override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                    Log.d(TAG, "← Received binary message: ${bytes.size} bytes")
                }

                override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                    isConnected.set(false)
                    if (firstFailureTime == null) firstFailureTime = System.currentTimeMillis()
                    Log.w(TAG, "⚠ WebSocket closing: $code / $reason")
                    updateStatus(ConnectionStatus(
                        isConnected = false,
                        lastError = "Connection closing: $reason (code: $code)",
                        lastErrorTime = System.currentTimeMillis(),
                        firstFailureTime = firstFailureTime,
                        connectionAttempts = connectionAttempts,
                        serverUrl = serverUrl,
                        responseCode = code,
                        retryCount = retryCount
                    ))
                    scheduleReconnect()
                }

                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                    isConnected.set(false)
                    if (firstFailureTime == null) firstFailureTime = System.currentTimeMillis()
                    Log.w(TAG, "✗ WebSocket closed: $code / $reason")
                    updateStatus(ConnectionStatus(
                        isConnected = false,
                        lastError = "Connection closed: $reason (code: $code)",
                        lastErrorTime = System.currentTimeMillis(),
                        firstFailureTime = firstFailureTime,
                        connectionAttempts = connectionAttempts,
                        serverUrl = serverUrl,
                        responseCode = code,
                        retryCount = retryCount
                    ))
                    scheduleReconnect()
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    isConnected.set(false)
                    if (firstFailureTime == null) firstFailureTime = System.currentTimeMillis()
                    val errorMsg = when (t) {
                        is java.net.ConnectException -> "Cannot reach server"
                        is java.net.UnknownHostException -> "Cannot resolve hostname"
                        is java.net.SocketTimeoutException -> "Connection timeout"
                        is javax.net.ssl.SSLException -> "SSL/TLS error: ${t.message}"
                        else -> "${t.javaClass.simpleName}: ${t.message}"
                    }
                    Log.w(TAG, "✗ WebSocket connection failed: $errorMsg | url=$serverUrl")
                    updateStatus(ConnectionStatus(
                        isConnected = false,
                        lastError = errorMsg,
                        lastErrorTime = System.currentTimeMillis(),
                        firstFailureTime = firstFailureTime,
                        connectionAttempts = connectionAttempts,
                        serverUrl = serverUrl,
                        responseCode = response?.code,
                        networkError = t.javaClass.simpleName,
                        retryCount = retryCount
                    ))
                    scheduleReconnect()
                }
            })
        } catch (e: Exception) {
            if (firstFailureTime == null) firstFailureTime = System.currentTimeMillis()
            Log.e(TAG, "✗ Failed to create WebSocket", e)
            updateStatus(ConnectionStatus(
                isConnected = false,
                lastError = "Failed to create WebSocket: ${e.message}",
                lastErrorTime = System.currentTimeMillis(),
                firstFailureTime = firstFailureTime,
                connectionAttempts = connectionAttempts,
                serverUrl = serverUrl,
                networkError = e.javaClass.simpleName,
                retryCount = retryCount
            ))
            scheduleReconnect()
        }
    }

    private fun scheduleReconnect() {
        if (!autoReconnectEnabled || isReconnecting) {
            return
        }
        
        isReconnecting = true
        retryCount++
        
        val delay = getRetryDelay(retryCount)
        Log.i(TAG, "Scheduling reconnect #$retryCount in ${delay}ms")
        
        // Update status to show we're retrying
        updateStatus(connectionStatus.copy(
            isRetrying = true,
            retryCount = retryCount,
            nextRetryInMs = delay
        ))
        
        reconnectHandler.postDelayed({
            Log.i(TAG, "Auto-reconnect attempt #$retryCount")
            // Reset isReconnecting before attempting to connect so subsequent failures can retry
            isReconnecting = false
            connect()
        }, delay)
    }

    fun disconnect() {
        // Disable auto-reconnect when manually disconnecting
        autoReconnectEnabled = false
        reconnectHandler.removeCallbacksAndMessages(null)
        
        webSocket?.close(1000, "Client disconnect")
        webSocket = null
        isConnected.set(false)
        isReconnecting = false
        retryCount = 0
        
        Log.i(TAG, "WebSocket disconnected")
    }
    fun sendFrame(frame: PerceiverDataFrame) {
        if (!isConnected.get()) {
            // Don't log this as it would spam the logs during reconnection
            framesDropped++
            return
        }

        // Serialize to bytes
        val bytes = frame.toByteArray()

        // Send via WebSocket
        val sent = webSocket?.send(ByteString.of(*bytes)) ?: false

        if (sent) {
            framesSent++
            bytesSent += bytes.size
        } else {
            framesDropped++
            if (framesSent % 30 == 0) {  // Log every 30th frame
                Log.w(TAG, "Failed to send frame")
            }
        }
    }

    fun startRecording(recordingName: String) {
        activeRecordingName = recordingName
        sendControl("start_recording", recordingName)
    }

    fun finishRecording(recordingName: String) {
        sendControl("finish_recording", recordingName)
        activeRecordingName = null
    }

    fun deleteRecording(recordingName: String) {
        sendControl("delete_recording", recordingName)
        activeRecordingName = null
    }

    private fun sendControl(action: String, recordingName: String) {
        if (!isConnected.get()) return
        val payload = JSONObject()
            .put("type", action)
            .put("recording_name", recordingName)
            .toString()
        if (webSocket?.send(payload) != true) {
            Log.w(TAG, "Failed to send control message: $action")
        }
    }

}
