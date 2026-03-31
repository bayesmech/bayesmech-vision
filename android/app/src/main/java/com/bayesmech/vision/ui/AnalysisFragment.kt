package com.bayesmech.vision.ui

import android.graphics.BitmapFactory
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.Editable
import android.text.TextWatcher
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.SeekBar
import android.widget.TextView
import androidx.activity.OnBackPressedCallback
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.fragment.app.Fragment
import androidx.fragment.app.activityViewModels
import androidx.lifecycle.lifecycleScope
import com.bayesmech.vision.AppViewModel
import com.bayesmech.vision.DataList
import com.bayesmech.vision.GensparkSummary
import com.bayesmech.vision.InsightVideoResponse
import com.bayesmech.vision.MainActivity
import com.bayesmech.vision.R
import com.bayesmech.vision.databinding.FragmentAnalysisBinding
import com.google.android.material.tabs.TabLayout
import io.noties.markwon.Markwon
import io.noties.markwon.ext.tables.TablePlugin
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.concurrent.TimeUnit

class AnalysisFragment : Fragment() {

    companion object {
        private const val ARG_RECORDING = "recording_bytes"
        private const val TAB_VIDEO = 0
        private const val TAB_UNDERSTANDING = 1

        fun newInstance(recording: DataList): AnalysisFragment {
            return AnalysisFragment().apply {
                arguments = Bundle().apply {
                    putByteArray(ARG_RECORDING, recording.toByteArray())
                }
            }
        }
    }

    private var _binding: FragmentAnalysisBinding? = null
    private val binding get() = _binding!!

    private val viewModel: AppViewModel by activityViewModels()
    private lateinit var recording: DataList

    private val httpClient = OkHttpClient.Builder()
        .readTimeout(120, TimeUnit.SECONDS)
        .build()

    // ── Video state ───────────────────────────────────────────────────────────

    /** JPEG frame bytes for each layer; null = not yet fetched. */
    private var framesRaw: List<ByteArray>? = null
    private var framesUnderstanding: List<ByteArray>? = null

    private var fps: Float = 15f
    private var currentFrameIndex: Int = 0
    private var isPlaying: Boolean = false
    private var activeLayer: Int = TAB_VIDEO

    // ── Chat state ──────────────────────────────────────────────────────────

    private var sessionId: String? = null
    private var loadingBubble: View? = null
    private lateinit var markwon: Markwon

    private val playHandler = Handler(Looper.getMainLooper())
    private val playRunnable = object : Runnable {
        override fun run() {
            val frames = activeFrames() ?: return
            if (!isPlaying) return
            if (currentFrameIndex < frames.size - 1) {
                advanceFrame(currentFrameIndex + 1)
                playHandler.postDelayed(this, (1000f / fps).toLong())
            } else {
                setPlaying(false)
            }
        }
    }

    // ── Lifecycle ─────────────────────────────────────────────────────────────

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val bytes = requireArguments().getByteArray(ARG_RECORDING)!!
        recording = DataList.parseFrom(bytes)

        requireActivity().onBackPressedDispatcher.addCallback(
            this,
            object : OnBackPressedCallback(true) {
                override fun handleOnBackPressed() {
                    (activity as? MainActivity)?.exitAnalysis()
                }
            }
        )
    }

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        _binding = FragmentAnalysisBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        markwon = Markwon.builder(requireContext())
            .usePlugin(TablePlugin.create(requireContext()))
            .build()

        binding.backButton.setOnClickListener {
            (activity as? MainActivity)?.exitAnalysis()
        }

        setupKeyboardHandling()
        populatePreview()
        setupVideoControls()
        setupTabSwitching()
        setupInput()
        fetchSummary()
        fetchVideoLayer(TAB_VIDEO)
    }

    override fun onPause() {
        super.onPause()
        setPlaying(false)
    }

    override fun onDestroyView() {
        super.onDestroyView()
        playHandler.removeCallbacksAndMessages(null)
        _binding = null
    }

    // ── Preview header ────────────────────────────────────────────────────────

    private fun populatePreview() {
        val rawName = recording.fileName
        val parsedDate = try {
            val parser = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US)
            val formatter = SimpleDateFormat("MMM dd, yyyy - HH:mm:ss", Locale.US)
            formatter.format(parser.parse(rawName)!!)
        } catch (_: Exception) {
            rawName
        }
        binding.previewTitle.text = parsedDate

        if (!recording.imageFrame.isEmpty) {
            val bytes = recording.imageFrame.toByteArray()
            binding.previewImage.setImageBitmap(BitmapFactory.decodeByteArray(bytes, 0, bytes.size))
        }

        binding.previewStatusSeg.setTextColor(if (recording.isSegmentationAvailable) Color.GREEN else Color.GRAY)
        binding.previewStatusGen.setTextColor(if (recording.isGensparkAvailable) Color.GREEN else Color.GRAY)
        binding.previewStatusMot.setTextColor(if (recording.isMotioncapAvailable) Color.GREEN else Color.GRAY)
    }

    // ── Video controls ────────────────────────────────────────────────────────

    private fun setupVideoControls() {
        binding.playPauseButton.setOnClickListener {
            if (activeFrames() == null) return@setOnClickListener
            setPlaying(!isPlaying)
        }

        binding.videoSeekbar.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(seekBar: SeekBar, progress: Int, fromUser: Boolean) {
                if (!fromUser) return
                val frames = activeFrames() ?: return
                val target = ((progress / 100f) * (frames.size - 1)).toInt().coerceIn(0, frames.size - 1)
                advanceFrame(target, updateSeekBar = false)
            }
            override fun onStartTrackingTouch(seekBar: SeekBar) { setPlaying(false) }
            override fun onStopTrackingTouch(seekBar: SeekBar) {}
        })
    }

    private fun setupTabSwitching() {
        binding.videoTabs.addOnTabSelectedListener(object : TabLayout.OnTabSelectedListener {
            override fun onTabSelected(tab: TabLayout.Tab) {
                activeLayer = tab.position
                val frames = activeFrames()
                if (frames != null) {
                    renderFrame(currentFrameIndex)
                } else if (tab.position == TAB_UNDERSTANDING) {
                    binding.videoLoading.text = "Loading understanding…"
                    binding.videoLoading.visibility = View.VISIBLE
                    fetchVideoLayer(TAB_UNDERSTANDING)
                }
            }
            override fun onTabUnselected(tab: TabLayout.Tab) {}
            override fun onTabReselected(tab: TabLayout.Tab) {}
        })
    }

    private fun setPlaying(playing: Boolean) {
        isPlaying = playing
        binding.playPauseButton.setImageResource(
            if (playing) android.R.drawable.ic_media_pause
            else android.R.drawable.ic_media_play
        )
        if (playing) {
            playHandler.post(playRunnable)
        } else {
            playHandler.removeCallbacks(playRunnable)
        }
    }

    /** Advance to a frame index and update UI. */
    private fun advanceFrame(index: Int, updateSeekBar: Boolean = true) {
        currentFrameIndex = index
        renderFrame(index)
        if (updateSeekBar) {
            val frames = activeFrames() ?: return
            val progress = if (frames.size <= 1) 0
            else ((index.toFloat() / (frames.size - 1)) * 100).toInt()
            binding.videoSeekbar.progress = progress
        }
        updateTimeLabel(index)
    }

    private fun renderFrame(index: Int) {
        val frames = activeFrames() ?: return
        if (index >= frames.size) return
        val jpeg = frames[index]
        val bitmap = BitmapFactory.decodeByteArray(jpeg, 0, jpeg.size) ?: return
        binding.videoFrameView.setImageBitmap(bitmap)
    }

    private fun updateTimeLabel(index: Int) {
        val frames = activeFrames() ?: return
        val seconds = if (fps > 0) index / fps else 0f
        val total = if (fps > 0) (frames.size - 1) / fps else 0f
        binding.videoTime.text = "${formatTime(seconds)} / ${formatTime(total)}"
    }

    private fun formatTime(seconds: Float): String {
        val s = seconds.toInt()
        return "${s / 60}:${(s % 60).toString().padStart(2, '0')}"
    }

    private fun activeFrames(): List<ByteArray>? =
        if (activeLayer == TAB_UNDERSTANDING) framesUnderstanding else framesRaw

    // ── Video fetching ────────────────────────────────────────────────────────

    private fun fetchVideoLayer(layer: Int) {
        val layerName = if (layer == TAB_UNDERSTANDING) "understanding" else "raw"
        viewLifecycleOwner.lifecycleScope.launch {
            val videoResp = withContext(Dispatchers.IO) {
                runCatching {
                    val httpBase = viewModel.serverUrl.value.trimEnd('/')
                        .replaceFirst("wss://", "https://")
                        .replaceFirst("ws://", "http://")
                    val url = "$httpBase/api/insightgen/video?file=${recording.fileName}&layer=$layerName"
                    val req = Request.Builder().url(url).get().build()
                    val resp = httpClient.newCall(req).execute()
                    if (!resp.isSuccessful) return@runCatching null
                    val bytes = resp.body?.bytes() ?: return@runCatching null
                    InsightVideoResponse.parseFrom(bytes)
                }.getOrNull()
            }

            if (videoResp == null || videoResp.framesCount == 0) {
                if (activeLayer == layer) {
                    binding.videoLoading.text = if (layer == TAB_UNDERSTANDING)
                        "No motion capture available." else "Video unavailable."
                }
                return@launch
            }

            val jpegList = videoResp.framesList.map { it.jpegData.toByteArray() }

            if (layer == TAB_VIDEO) {
                framesRaw = jpegList
                fps = if (videoResp.fps > 0) videoResp.fps else 15f
            } else {
                framesUnderstanding = jpegList
            }

            // Only update UI if this layer is currently selected
            if (activeLayer == layer) {
                binding.videoLoading.visibility = View.GONE
                advanceFrame(currentFrameIndex.coerceIn(0, jpegList.size - 1))
                binding.videoSeekbar.max = 100
                updateTimeLabel(currentFrameIndex)
            }
        }
    }

    // ── Summary ───────────────────────────────────────────────────────────────

    private fun fetchSummary() {
        viewLifecycleOwner.lifecycleScope.launch {
            val summary = withContext(Dispatchers.IO) {
                runCatching {
                    val httpBase = viewModel.serverUrl.value.trimEnd('/')
                        .replaceFirst("wss://", "https://")
                        .replaceFirst("ws://", "http://")
                    val url = "$httpBase/api/insightgen/insight?file=${recording.fileName}"
                    val req = Request.Builder().url(url).get().build()
                    val resp = httpClient.newCall(req).execute()
                    if (!resp.isSuccessful) return@runCatching null
                    val bytes = resp.body?.bytes() ?: return@runCatching null
                    GensparkSummary.parseFrom(bytes)
                }.getOrNull()
            }

            if (summary == null || (summary.title.isBlank() && summary.text.isBlank())) {
                binding.summaryLoading.text = "No analysis available yet."
                return@launch
            }

            if (summary.title.isNotBlank()) {
                binding.summaryTitle.text = summary.title
                binding.summaryTitle.visibility = View.VISIBLE
            }

            markwon.setMarkdown(binding.summaryBody, buildMarkdown(summary))
            binding.summaryBody.visibility = View.VISIBLE
            binding.summaryLoading.visibility = View.GONE
        }
    }

    private fun buildMarkdown(summary: GensparkSummary): String {
        val sb = StringBuilder()
        if (summary.text.isNotBlank()) sb.append(summary.text)
        if (summary.parametersCount > 0) {
            sb.append("\n\n| Parameter | Value | Unit |\n")
            sb.append("|:---|:---|:---|\n")
            for (p in summary.parametersList) {
                sb.append("| ${p.name} | ${p.value} | ${p.unit} |\n")
            }
        }
        return sb.toString()
    }

    // ── Keyboard handling ────────────────────────────────────────────────────

    private fun setupKeyboardHandling() {
        val frame = binding.summaryScroll.parent as View
        ViewCompat.setOnApplyWindowInsetsListener(frame) { v, insets ->
            val ime = insets.getInsets(WindowInsetsCompat.Type.ime())
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.setPadding(0, 0, 0, maxOf(ime.bottom, bars.bottom))
            if (ime.bottom > 0) {
                binding.summaryScroll.post {
                    binding.summaryScroll.fullScroll(View.FOCUS_DOWN)
                }
            }
            insets
        }
    }

    // ── Input bar + chat ─────────────────────────────────────────────────────

    private fun setupInput() {
        binding.messageInput.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
            override fun afterTextChanged(s: Editable?) {
                val hasText = !s.isNullOrBlank()
                binding.sendButton.isEnabled = hasText
                binding.sendButton.alpha = if (hasText) 1f else 0.4f
            }
        })

        binding.sendButton.setOnClickListener {
            val text = binding.messageInput.text?.toString()?.trim() ?: return@setOnClickListener
            if (text.isBlank()) return@setOnClickListener
            binding.messageInput.text?.clear()
            addChatBubble(text, isUser = true)
            addLoadingBubble()
            sendFollowUp(text)
        }
    }

    // ── Chat bubbles ─────────────────────────────────────────────────────────

    private fun addChatBubble(text: String, isUser: Boolean) {
        binding.chatContainer.visibility = View.VISIBLE

        val wrapper = LinearLayout(requireContext()).apply {
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { bottomMargin = dpToPx(6) }
            gravity = if (isUser) Gravity.END else Gravity.START
        }

        val bubble = TextView(requireContext()).apply {
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            )
            maxWidth = dpToPx(280)
            setPadding(dpToPx(12), dpToPx(8), dpToPx(12), dpToPx(8))
            setBackgroundResource(
                if (isUser) R.drawable.chat_bubble_user else R.drawable.chat_bubble_ai
            )
            setTextColor(Color.WHITE)
            textSize = 15f
            setLineSpacing(0f, 1.3f)
        }

        if (isUser) {
            bubble.text = text
        } else {
            markwon.setMarkdown(bubble, text)
        }

        wrapper.addView(bubble)
        binding.chatContainer.addView(wrapper)
        scrollToBottom()
    }

    private fun addLoadingBubble() {
        binding.chatContainer.visibility = View.VISIBLE

        val wrapper = LinearLayout(requireContext()).apply {
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ).apply { bottomMargin = dpToPx(6) }
            gravity = Gravity.START
        }

        val bubble = TextView(requireContext()).apply {
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            )
            setPadding(dpToPx(12), dpToPx(8), dpToPx(12), dpToPx(8))
            setBackgroundResource(R.drawable.chat_bubble_ai)
            setTextColor(Color.parseColor("#9CA3AF"))
            textSize = 15f
            this.text = "Thinking…"
        }

        wrapper.addView(bubble)
        binding.chatContainer.addView(wrapper)
        loadingBubble = wrapper
        scrollToBottom()
    }

    private fun removeLoadingBubble() {
        loadingBubble?.let { binding.chatContainer.removeView(it) }
        loadingBubble = null
    }

    private fun scrollToBottom() {
        binding.summaryScroll.post {
            binding.summaryScroll.fullScroll(View.FOCUS_DOWN)
        }
    }

    private fun dpToPx(dp: Int): Int =
        (dp * resources.displayMetrics.density).toInt()

    // ── Follow-up API call ───────────────────────────────────────────────────

    private fun sendFollowUp(text: String) {
        viewLifecycleOwner.lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) {
                runCatching {
                    val httpBase = viewModel.serverUrl.value.trimEnd('/')
                        .replaceFirst("wss://", "https://")
                        .replaceFirst("ws://", "http://")
                    val url = "$httpBase/api/insightgen/chat"

                    val jsonBody = JSONObject().apply {
                        put("file", recording.fileName)
                        put("message", text)
                        sessionId?.let { put("session_id", it) }
                    }

                    val body = jsonBody.toString()
                        .toRequestBody("application/json".toMediaType())
                    val req = Request.Builder().url(url).post(body).build()
                    val resp = httpClient.newCall(req).execute()
                    if (!resp.isSuccessful) return@runCatching null
                    val respBody = resp.body?.string() ?: return@runCatching null
                    JSONObject(respBody)
                }.getOrNull()
            }

            removeLoadingBubble()

            if (result != null) {
                sessionId = result.optString("session_id", sessionId)
                val aiText = result.getString("response")
                addChatBubble(aiText, isUser = false)
            } else {
                addChatBubble("Failed to get response. Please try again.", isUser = false)
            }
        }
    }
}
