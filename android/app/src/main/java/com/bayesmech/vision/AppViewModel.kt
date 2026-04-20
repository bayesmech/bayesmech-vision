package com.bayesmech.vision

import android.app.Application
import android.content.Context
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.bayesmech.vision.coverage.CoverageStats
import com.bayesmech.vision.network.ConnectionStatus
import com.bayesmech.vision.ListRecordingsRequest
import com.bayesmech.vision.ListRecordingsResponse
import com.bayesmech.vision.DataList
import com.google.android.gms.auth.api.signin.GoogleSignIn
import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

class AppViewModel(application: Application) : AndroidViewModel(application) {

    private val prefs = application.getSharedPreferences("app_settings", Context.MODE_PRIVATE)

    // ── Server ──────────────────────────────────────────────────────────────

    private val _serverUrl = MutableStateFlow(
        prefs.getString("server_url", "ws://192.168.1.2:8080")
            ?: "ws://192.168.1.2:8080"
    )
    val serverUrl: StateFlow<String> = _serverUrl.asStateFlow()

    fun setServerUrl(url: String) {
        _serverUrl.value = url
        prefs.edit().putString("server_url", url).apply()
    }

    // ── Data selection settings ──────────────────────────────────────────────

    private val _enableDepthData = MutableStateFlow(prefs.getBoolean("enable_depth", true))
    val enableDepthData: StateFlow<Boolean> = _enableDepthData.asStateFlow()

    fun setEnableDepthData(enabled: Boolean) {
        _enableDepthData.value = enabled
        prefs.edit().putBoolean("enable_depth", enabled).apply()
    }

    private val _enableInferredGeometry = MutableStateFlow(prefs.getBoolean("enable_geometry", false))
    val enableInferredGeometry: StateFlow<Boolean> = _enableInferredGeometry.asStateFlow()

    fun setEnableInferredGeometry(enabled: Boolean) {
        _enableInferredGeometry.value = enabled
        prefs.edit().putBoolean("enable_geometry", enabled).apply()
    }

    // ── Visualization settings ───────────────────────────────────────────────

    private val _visualizeDepthMap = MutableStateFlow(prefs.getBoolean("viz_depth", false))
    val visualizeDepthMap: StateFlow<Boolean> = _visualizeDepthMap.asStateFlow()

    fun setVisualizeDepthMap(enabled: Boolean) {
        _visualizeDepthMap.value = enabled
        prefs.edit().putBoolean("viz_depth", enabled).apply()
    }

    private val _visualizePointCloud = MutableStateFlow(prefs.getBoolean("viz_points", true))
    val visualizePointCloud: StateFlow<Boolean> = _visualizePointCloud.asStateFlow()

    fun setVisualizePointCloud(enabled: Boolean) {
        _visualizePointCloud.value = enabled
        prefs.edit().putBoolean("viz_points", enabled).apply()
    }

    private val _visualizePlanes = MutableStateFlow(prefs.getBoolean("viz_planes", false))
    val visualizePlanes: StateFlow<Boolean> = _visualizePlanes.asStateFlow()

    fun setVisualizePlanes(enabled: Boolean) {
        _visualizePlanes.value = enabled
        prefs.edit().putBoolean("viz_planes", enabled).apply()
    }

    // ── Connection status (written by MainActivity, read by SettingsFragment) ─

    private val _connectionStatus = MutableStateFlow<ConnectionStatus?>(null)
    val connectionStatus: StateFlow<ConnectionStatus?> = _connectionStatus.asStateFlow()

    fun updateConnectionStatus(status: ConnectionStatus) {
        _connectionStatus.value = status
    }

    // ── Recording state ──────────────────────────────────────────────────────

    private val _isRecording = MutableStateFlow(false)
    val isRecording: StateFlow<Boolean> = _isRecording.asStateFlow()

    fun setRecording(recording: Boolean) {
        _isRecording.value = recording
    }

    private val _draftUserText = MutableStateFlow("")
    val draftUserText: StateFlow<String> = _draftUserText.asStateFlow()

    fun setDraftUserText(text: String) {
        _draftUserText.value = text
        if (text.isNotBlank()) {
            clearTranscriptStatusMessage()
        }
    }

    fun clearDraftUserText() {
        _draftUserText.value = ""
    }

    private val _isMicRecording = MutableStateFlow(false)
    val isMicRecording: StateFlow<Boolean> = _isMicRecording.asStateFlow()

    fun setMicRecording(recording: Boolean) {
        _isMicRecording.value = recording
    }

    private val _isTranscribing = MutableStateFlow(false)
    val isTranscribing: StateFlow<Boolean> = _isTranscribing.asStateFlow()

    fun setTranscribing(transcribing: Boolean) {
        _isTranscribing.value = transcribing
    }

    private val _transcriptStatusMessage = MutableStateFlow<String?>(null)
    val transcriptStatusMessage: StateFlow<String?> = _transcriptStatusMessage.asStateFlow()

    fun setTranscriptStatusMessage(message: String?) {
        _transcriptStatusMessage.value = message
    }

    fun clearTranscriptStatusMessage() {
        _transcriptStatusMessage.value = null
    }

    // ── Coverage stats (written by MainActivity via CoverageTracker) ──────────

    private val _coverageStats = MutableStateFlow(CoverageStats())
    val coverageStats: StateFlow<CoverageStats> = _coverageStats.asStateFlow()

    fun updateCoverageStats(stats: CoverageStats) {
        _coverageStats.value = stats
    }

    // ── Recordings cache ─────────────────────────────────────────────────────

    private val _recordings = MutableStateFlow<List<DataList>>(emptyList())
    val recordings: StateFlow<List<DataList>> = _recordings.asStateFlow()

    private val _isRefreshing = MutableStateFlow(false)
    val isRefreshing: StateFlow<Boolean> = _isRefreshing.asStateFlow()

    private val httpClient = OkHttpClient()

    init {
        // Load persisted recordings so the library is usable immediately on first open
        viewModelScope.launch(Dispatchers.IO) {
            try {
                val file = File(getApplication<Application>().filesDir, "recordings_cache.pb")
                if (file.exists()) {
                    val pb = ListRecordingsResponse.parseFrom(file.readBytes())
                    _recordings.value = pb.recordingsList
                }
            } catch (_: Exception) {}
        }
    }

    fun fetchRecordings() {
        _isRefreshing.value = true
        viewModelScope.launch(Dispatchers.IO) {
            try {
                val account = GoogleSignIn.getLastSignedInAccount(getApplication())
                val reqProto = ListRecordingsRequest.newBuilder()
                    .setUsername(account?.displayName ?: account?.email ?: "unknown")
                    .setAuthToken(account?.id ?: "")
                    .build()
                val body = reqProto.toByteArray().toRequestBody("application/x-protobuf".toMediaType())
                val httpBase = serverUrl.value.trimEnd('/')
                    .replaceFirst("wss://", "https://")
                    .replaceFirst("ws://", "http://")
                val request = Request.Builder()
                    .url("$httpBase/api/insightgen/recordings")
                    .post(body)
                    .build()
                val response = httpClient.newCall(request).execute()
                if (response.isSuccessful) {
                    val stream = response.body?.byteStream() ?: run {
                        _isRefreshing.value = false
                        return@launch
                    }
                    val pb = ListRecordingsResponse.parseFrom(stream)
                    _recordings.value = pb.recordingsList
                    // Persist to disk for offline use
                    try {
                        val file = File(getApplication<Application>().filesDir, "recordings_cache.pb")
                        file.writeBytes(pb.toByteArray())
                    } catch (_: Exception) {}
                }
            } catch (_: Exception) {}
            _isRefreshing.value = false
        }
    }

    // ── ARCore / rendering errors (logged, not toasted) ───────────────────────

    private val _arcoreError = MutableStateFlow<String?>(null)
    val arcoreError: StateFlow<String?> = _arcoreError.asStateFlow()

    fun setArcoreError(message: String) {
        _arcoreError.value = message
    }

    fun clearArcoreError() {
        _arcoreError.value = null
    }
}
