package com.bayesmech.vision.ui

import android.Manifest
import android.animation.ObjectAnimator
import android.animation.ValueAnimator
import android.content.pm.PackageManager
import android.media.MediaRecorder
import android.os.Bundle
import android.text.Layout
import android.text.StaticLayout
import android.util.Log
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.animation.AccelerateDecelerateInterpolator
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.core.view.doOnLayout
import androidx.fragment.app.Fragment
import androidx.fragment.app.activityViewModels
import androidx.lifecycle.lifecycleScope
import com.bayesmech.vision.AppViewModel
import com.bayesmech.vision.MainActivity
import com.bayesmech.vision.R
import com.bayesmech.vision.audio.StreamlogTranscriptionClient
import com.bayesmech.vision.databinding.FragmentCameraBinding
import java.io.File
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch

class CameraFragment : Fragment() {

    private var _binding: FragmentCameraBinding? = null
    private val binding get() = _binding!!

    private val viewModel: AppViewModel by activityViewModels()
    private val transcriptionClient = StreamlogTranscriptionClient()

    private var timerJob: Job? = null
    private var recordingStartMs = 0L
    private var pulseAnimator: ObjectAnimator? = null
    private var isFullscreen = false
    private var micRecorder: MediaRecorder? = null
    private var micOutputFile: File? = null
    private var lastRenderedLabelText: String = ""
    private var lastRenderedLabelColor: Int = R.color.text_secondary

    private val audioPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) {
            startMicRecording()
        } else {
            viewModel.setTranscriptStatusMessage(getString(R.string.camera_transcript_mic_denied))
        }
    }

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        _binding = FragmentCameraBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        binding.recordButton.setOnClickListener {
            val isRecording = viewModel.isRecording.value
            val mainActivity = activity as? MainActivity ?: return@setOnClickListener

            if (isRecording) {
                mainActivity.recordingManager.stopRecording()
                viewModel.setRecording(false)
            } else {
                val filename = mainActivity.recordingManager.startRecording()
                if (filename != null) {
                    viewModel.setRecording(true)
                }
            }
        }

        binding.fullscreenButton.setOnClickListener { toggleFullscreen() }
        binding.micButton.setOnClickListener { handleMicButtonClick() }
        binding.sendButton.setOnClickListener { sendTranscript() }
        binding.messageLabel.doOnLayout {
            applyLabelText(lastRenderedLabelText, lastRenderedLabelColor)
        }

        viewLifecycleOwner.lifecycleScope.launch {
            viewModel.isRecording.collect { recording ->
                updateRecordButtonState(recording)
                if (recording) {
                    recordingStartMs = System.currentTimeMillis()
                    startTimer()
                } else {
                    stopTimer()
                    binding.recordingTimer.text = ""
                    binding.recordingTimer.visibility = View.GONE
                }
            }
        }

        viewLifecycleOwner.lifecycleScope.launch {
            combine(
                viewModel.draftUserText,
                viewModel.isMicRecording,
                viewModel.isTranscribing,
                viewModel.transcriptStatusMessage
            ) { draftText, isMicRecording, isTranscribing, statusMessage ->
                TranscriptUiState(
                    draftText = draftText,
                    isMicRecording = isMicRecording,
                    isTranscribing = isTranscribing,
                    statusMessage = statusMessage
                )
            }.collect { state ->
                renderTranscriptUi(state)
            }
        }
    }

    private fun handleMicButtonClick() {
        if (viewModel.isTranscribing.value) {
            return
        }

        if (viewModel.isMicRecording.value) {
            stopMicRecordingAndTranscribe()
            return
        }

        if (viewModel.serverUrl.value.isBlank()) {
            viewModel.setTranscriptStatusMessage(getString(R.string.camera_transcript_unavailable))
            return
        }

        if (ContextCompat.checkSelfPermission(
                requireContext(),
                Manifest.permission.RECORD_AUDIO
            ) == PackageManager.PERMISSION_GRANTED
        ) {
            startMicRecording()
        } else {
            audioPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    private fun startMicRecording() {
        try {
            val outputFile = File.createTempFile("voice_note_", ".m4a", requireContext().cacheDir)
            releaseMicRecorder()
            micRecorder = MediaRecorder().apply {
                setAudioSource(MediaRecorder.AudioSource.MIC)
                setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
                setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
                setAudioChannels(1)
                setAudioSamplingRate(44100)
                setAudioEncodingBitRate(128000)
                setOutputFile(outputFile.absolutePath)
                prepare()
                start()
            }
            micOutputFile = outputFile
            viewModel.setMicRecording(true)
            viewModel.clearTranscriptStatusMessage()
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start microphone recording", e)
            micOutputFile?.delete()
            releaseMicRecorder()
            viewModel.setMicRecording(false)
            viewModel.setTranscriptStatusMessage(getString(R.string.camera_transcript_failed))
        }
    }

    private fun stopMicRecordingAndTranscribe() {
        val recorder = micRecorder ?: return
        val outputFile = micOutputFile
        var recordingStopped = false

        try {
            recorder.stop()
            recordingStopped = true
        } catch (e: RuntimeException) {
            Log.w(TAG, "Voice note recording was too short", e)
        } finally {
            releaseMicRecorder()
            viewModel.setMicRecording(false)
        }

        if (!recordingStopped || outputFile == null || !outputFile.exists() || outputFile.length() == 0L) {
            outputFile?.delete()
            viewModel.setTranscriptStatusMessage(getString(R.string.camera_transcript_too_short))
            return
        }

        transcribeAudio(outputFile)
    }

    private fun transcribeAudio(audioFile: File) {
        val serverUrl = viewModel.serverUrl.value.trim()
        if (serverUrl.isBlank()) {
            audioFile.delete()
            viewModel.setTranscriptStatusMessage(getString(R.string.camera_transcript_unavailable))
            return
        }

        viewModel.setTranscribing(true)
        viewModel.clearTranscriptStatusMessage()

        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val transcript = transcriptionClient.transcribe(audioFile, serverUrl)
                viewModel.setDraftUserText(transcript)
            } catch (e: Exception) {
                Log.e(TAG, "Failed to transcribe voice note", e)
                viewModel.setTranscriptStatusMessage(getString(R.string.camera_transcript_failed))
            } finally {
                viewModel.setTranscribing(false)
                audioFile.delete()
            }
        }
    }

    private fun sendTranscript() {
        val text = viewModel.draftUserText.value.trim()
        if (text.isBlank() || viewModel.isMicRecording.value || viewModel.isTranscribing.value) {
            return
        }

        (activity as? MainActivity)?.renderer?.sendUserTextInput(text)
        viewModel.clearDraftUserText()
        viewModel.clearTranscriptStatusMessage()
    }

    private fun renderTranscriptUi(state: TranscriptUiState) {
        val labelText: String
        val labelColor: Int

        when {
            state.isMicRecording -> {
                labelText = getString(R.string.camera_transcript_recording)
                labelColor = R.color.text_secondary
            }
            state.isTranscribing -> {
                labelText = getString(R.string.camera_transcript_transcribing)
                labelColor = R.color.text_secondary
            }
            !state.statusMessage.isNullOrBlank() -> {
                labelText = state.statusMessage.orEmpty()
                labelColor = R.color.text_secondary
            }
            state.draftText.isNotBlank() -> {
                labelText = state.draftText
                labelColor = R.color.text_primary
            }
            else -> {
                labelText = getString(R.string.camera_transcript_placeholder)
                labelColor = R.color.text_secondary
            }
        }

        lastRenderedLabelText = labelText
        lastRenderedLabelColor = labelColor
        applyLabelText(labelText, labelColor)

        val canSend = state.draftText.isNotBlank() && !state.isMicRecording && !state.isTranscribing
        binding.sendButton.isEnabled = canSend
        binding.sendButton.alpha = if (canSend) 1f else 0.4f

        binding.micButton.isEnabled = !state.isTranscribing
        binding.micButton.alpha = if (state.isTranscribing) 0.6f else 1f
        binding.micButton.setBackgroundResource(
            if (state.isMicRecording) {
                R.drawable.mic_button_background_active
            } else {
                R.drawable.mic_button_background
            }
        )
    }

    private fun applyLabelText(text: String, colorRes: Int) {
        val textView = _binding?.messageLabel ?: return
        textView.setTextColor(ContextCompat.getColor(requireContext(), colorRes))
        textView.text = truncateToTrailingLines(textView, text)
    }

    private fun truncateToTrailingLines(textView: TextView, text: String): String {
        if (text.isBlank()) {
            return text
        }

        val availableWidth = textView.width - textView.paddingLeft - textView.paddingRight
        if (availableWidth <= 0) {
            return text
        }

        if (fitsWithinLines(textView, text, availableWidth)) {
            return text
        }

        val prefix = "... "
        var low = 0
        var high = text.length
        var best = prefix

        while (low <= high) {
            val mid = (low + high) / 2
            val candidate = prefix + text.takeLast(mid)
            if (fitsWithinLines(textView, candidate, availableWidth)) {
                best = candidate
                low = mid + 1
            } else {
                high = mid - 1
            }
        }

        return best
    }

    private fun fitsWithinLines(textView: TextView, text: String, width: Int): Boolean {
        val layout = StaticLayout.Builder
            .obtain(text, 0, text.length, textView.paint, width)
            .setAlignment(Layout.Alignment.ALIGN_NORMAL)
            .setIncludePad(false)
            .setLineSpacing(textView.lineSpacingExtra, textView.lineSpacingMultiplier)
            .build()

        val maxLines = textView.maxLines.coerceAtLeast(2)
        if (layout.lineCount > maxLines) {
            return false
        }

        return layout.getLineEnd(layout.lineCount - 1) == text.length
    }

    private fun toggleFullscreen() {
        isFullscreen = !isFullscreen
        if (isFullscreen) {
            binding.chatPanel.visibility = View.GONE
            binding.recordButton.visibility = View.GONE
            binding.fullscreenButton.setImageResource(R.drawable.ic_fullscreen_exit)
        } else {
            binding.chatPanel.visibility = View.VISIBLE
            binding.recordButton.visibility = View.VISIBLE
            binding.fullscreenButton.setImageResource(R.drawable.ic_fullscreen)
        }
    }

    private fun updateRecordButtonState(isRecording: Boolean) {
        if (isRecording) {
            binding.recordButton.setBackgroundResource(R.drawable.record_button_recording)
            binding.liveBadge.text = "● REC"
            binding.liveBadge.visibility = View.VISIBLE
            startPulse()
        } else {
            stopPulse()
            binding.recordButton.setBackgroundResource(R.drawable.record_button_idle)
            binding.liveBadge.visibility = View.GONE
        }
    }

    private fun startPulse() {
        stopPulse()
        pulseAnimator = ObjectAnimator.ofFloat(binding.recordButton, View.ALPHA, 1f, 0.5f).apply {
            duration = 1000L
            repeatMode = ValueAnimator.REVERSE
            repeatCount = ValueAnimator.INFINITE
            interpolator = AccelerateDecelerateInterpolator()
            start()
        }
    }

    private fun stopPulse() {
        pulseAnimator?.cancel()
        pulseAnimator = null
        binding.recordButton.alpha = 1f
    }

    private fun startTimer() {
        timerJob?.cancel()
        timerJob = viewLifecycleOwner.lifecycleScope.launch {
            binding.recordingTimer.visibility = View.VISIBLE
            while (true) {
                val elapsed = System.currentTimeMillis() - recordingStartMs
                val totalSeconds = (elapsed / 1000).toInt()
                val minutes = totalSeconds / 60
                val seconds = totalSeconds % 60
                binding.recordingTimer.text = "%d:%02d".format(minutes, seconds)
                delay(1000)
            }
        }
    }

    private fun stopTimer() {
        timerJob?.cancel()
        timerJob = null
    }

    private fun releaseMicRecorder() {
        runCatching { micRecorder?.reset() }
        runCatching { micRecorder?.release() }
        micRecorder = null
        micOutputFile = null
    }

    override fun onDestroyView() {
        super.onDestroyView()
        stopTimer()
        stopPulse()
        isFullscreen = false

        if (viewModel.isMicRecording.value) {
            try {
                micRecorder?.stop()
            } catch (_: Exception) {
            }
        }
        val orphanedFile = micOutputFile
        releaseMicRecorder()
        orphanedFile?.delete()
        viewModel.setMicRecording(false)

        _binding = null
    }

    private data class TranscriptUiState(
        val draftText: String,
        val isMicRecording: Boolean,
        val isTranscribing: Boolean,
        val statusMessage: String?
    )

    companion object {
        private const val TAG = "CameraFragment"
    }
}
