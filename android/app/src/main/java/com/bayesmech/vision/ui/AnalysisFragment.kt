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
import com.bayesmech.vision.ChatHistory
import com.bayesmech.vision.ChatTurn
import com.bayesmech.vision.DataList
import com.bayesmech.vision.GensparkSummary
import com.bayesmech.vision.InsightVideoResponse
import com.bayesmech.vision.MainActivity
import com.bayesmech.vision.R
import com.bayesmech.vision.analysis.InsightRepository
import com.bayesmech.vision.databinding.FragmentAnalysisBinding
import com.google.android.material.tabs.TabLayout
import android.util.TypedValue
import io.noties.markwon.Markwon
import io.noties.markwon.ext.latex.JLatexMathPlugin
import io.noties.markwon.ext.tables.TablePlugin
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Locale

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
    private lateinit var insightRepository: InsightRepository

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
    private val renderedChatTurnKeys = linkedSetOf<String>()

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
        insightRepository = InsightRepository(requireContext())

        val textSizePx = TypedValue.applyDimension(
            TypedValue.COMPLEX_UNIT_SP, 15f, resources.displayMetrics
        )
        markwon = Markwon.builder(requireContext())
            .usePlugin(TablePlugin.create(requireContext()))
            .usePlugin(JLatexMathPlugin.create(textSizePx) { builder ->
                builder.theme()
                    .blockFitCanvas(true)
                    .textColor(android.graphics.Color.WHITE)
            })
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
        fetchChatHistory()
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

    // ── Topbar ────────────────────────────────────────────────────────────────

    private fun populatePreview() {
        binding.previewTitle.text = recording.title.takeIf { it.isNotBlank() } ?: recording.fileName

        binding.topbarDate.text = try {
            val parser = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US)
            val formatter = SimpleDateFormat("MMM dd, yyyy · HH:mm", Locale.US)
            formatter.format(parser.parse(recording.fileName)!!)
        } catch (_: Exception) {
            recording.fileName
        }

        binding.topbarTagsRow.removeAllViews()
        recording.tagsList.filter { it.isNotBlank() }.forEach { tag ->
            val chip = android.widget.TextView(requireContext()).apply {
                text = tag
                textSize = 10.5f
                setTextColor(requireContext().getColor(R.color.text_secondary))
                setBackgroundResource(R.drawable.tag_chip_background)
                val ph = (8 * resources.displayMetrics.density).toInt()
                val pv = (2 * resources.displayMetrics.density).toInt()
                setPadding(ph, pv, ph, pv)
            }
            val params = android.widget.LinearLayout.LayoutParams(
                android.widget.LinearLayout.LayoutParams.WRAP_CONTENT,
                android.widget.LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { marginEnd = (6 * resources.displayMetrics.density).toInt() }
            binding.topbarTagsRow.addView(chip, params)
        }
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
            val videoResp = insightRepository.getVideoLayer(
                serverUrl = viewModel.serverUrl.value,
                fileName = recording.fileName,
                layerName = layerName,
            )

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
            val summary = insightRepository.getSummary(
                serverUrl = viewModel.serverUrl.value,
                fileName = recording.fileName,
            )

            if (summary == null || (summary.title.isBlank() && summary.text.isBlank() && summary.parametersCount == 0)) {
                val cachedHistory = insightRepository.readCachedChatHistory(recording.fileName)
                if (cachedHistory?.hasInitialTurn() == true && cachedHistory.initialTurn.text.isNotBlank()) {
                    renderSummary(markdown = cachedHistory.initialTurn.text)
                    return@launch
                }
                binding.summaryLoading.text = "No analysis available yet."
                return@launch
            }

            renderSummary(title = summary.title.takeIf { it.isNotBlank() }, markdown = buildMarkdown(summary))
        }
    }

    private fun renderSummary(title: String? = null, markdown: String) {
        binding.summaryTitle.visibility = if (title.isNullOrBlank()) View.GONE else View.VISIBLE
        if (!title.isNullOrBlank()) {
            binding.summaryTitle.text = title
        }
        markwon.setMarkdown(binding.summaryBody, preprocessLatex(markdown))
        binding.summaryBody.visibility = View.VISIBLE
        binding.summaryLoading.visibility = View.GONE
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

    private fun addChatBubble(text: String, isUser: Boolean, renderedKey: String? = null) {
        binding.chatContainer.visibility = View.VISIBLE
        renderedKey?.let { renderedChatTurnKeys.add(it) }

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
            markwon.setMarkdown(bubble, preprocessLatex(text))
        }

        wrapper.addView(bubble)
        binding.chatContainer.addView(wrapper)
        scrollToBottom()
    }

    private fun renderChatTurn(turn: ChatTurn) {
        val key = turn.renderKey()
        if (!renderedChatTurnKeys.add(key)) {
            return
        }
        addChatBubble(turn.text, isUser = turn.role == "user")
    }

    private fun renderChatTurns(turns: List<ChatTurn>) {
        turns.forEach(::renderChatTurn)
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

    private fun ChatTurn.renderKey(): String = "${timestampNs}|${role}|${text}"

    // ── Chat history fetch ───────────────────────────────────────────────────

    /**
     * Convert all LaTeX delimiters to the multi-line block format that
     * JLatexMathBlockParser reliably handles:
     *
     *   $$
     *   formula
     *   $$
     *
     * Why multi-line? Markwon's block parser only closes a $$ block when it
     * sees $$ at the START of a new line. A single-line "$$formula$$" is opened
     * (consuming "$$") but never closed, so the rest of the line becomes a raw
     * paragraph — causing the double-render. Multi-line avoids this entirely.
     *
     * The inline parser ($...$) is not used because JLatexMathInlineProcessor
     * silently returns null for Gemini's output patterns, leaving raw text.
     *
     * Handles:
     *  - \[...\]  display math (LaTeX standard)
     *  - \(...\)  inline math  (LaTeX standard)
     *  - $$...$$  single-line block
     *  - $...$    inline
     */
    private fun preprocessLatex(text: String): String {
        // Each replacement converts to "\n$$\nformula\n$$\n" (block element on its own lines)
        fun block(formula: String) = "\n\$\$\n${formula.trim()}\n\$\$\n"

        return text
            .replace(Regex("""\\\[([\s\S]+?)\\\]"""))    { block(it.groupValues[1]) }
            .replace(Regex("""\\\((.+?)\\\)"""))          { block(it.groupValues[1]) }
            .replace(Regex("""\$\$([^\n]+?)\$\$"""))      { block(it.groupValues[1]) }
            .replace(Regex("""\$([^$\n]+?)\$"""))         { block(it.groupValues[1]) }
    }

    private fun showSendButton() {
        binding.sendLoading.visibility = View.GONE
        binding.sendButton.visibility = View.VISIBLE
    }

    private fun fetchChatHistory() {
        viewLifecycleOwner.lifecycleScope.launch {
            insightRepository.readCachedChatHistory(recording.fileName)?.let { cachedHistory ->
                renderChatTurns(cachedHistory.turnsList)
            }

            val result = insightRepository.syncChatHistory(
                serverUrl = viewModel.serverUrl.value,
                fileName = recording.fileName,
            )

            result.history?.takeIf { it.hasInitialTurn() && binding.summaryBody.visibility != View.VISIBLE }?.let {
                renderSummary(markdown = it.initialTurn.text)
            }
            renderChatTurns(result.newTurns)

            showSendButton()
        }
    }

    // ── Follow-up API call ───────────────────────────────────────────────────

    private fun sendFollowUp(text: String) {
        viewLifecycleOwner.lifecycleScope.launch {
            val result = insightRepository.sendFollowUp(
                serverUrl = viewModel.serverUrl.value,
                fileName = recording.fileName,
                message = text,
                sessionId = sessionId,
            )

            removeLoadingBubble()

            if (result != null) {
                sessionId = result.sessionId
                insightRepository.cacheChatExchange(
                    fileName = recording.fileName,
                    userMessage = text,
                    responseText = result.responseText,
                    userTimestampNs = result.userTimestampNs,
                    responseTimestampNs = result.responseTimestampNs,
                )
                renderedChatTurnKeys.add("${result.userTimestampNs}|user|$text")
                addChatBubble(
                    result.responseText,
                    isUser = false,
                    renderedKey = "${result.responseTimestampNs}|model|${result.responseText}",
                )
            } else {
                addChatBubble("Failed to get response. Please try again.", isUser = false)
            }
        }
    }
}
