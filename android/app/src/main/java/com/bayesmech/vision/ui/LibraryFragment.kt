package com.bayesmech.vision.ui

import android.graphics.BitmapFactory
import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.fragment.app.Fragment
import androidx.fragment.app.activityViewModels
import androidx.lifecycle.lifecycleScope
import com.bayesmech.vision.AppViewModel
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.bayesmech.vision.R
import com.bayesmech.vision.databinding.FragmentLibraryBinding
import com.bayesmech.vision.DataList
import com.google.android.material.card.MaterialCardView
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Locale

class LibraryFragment : Fragment() {

    private var _binding: FragmentLibraryBinding? = null
    private val binding get() = _binding!!

    private val viewModel: AppViewModel by activityViewModels()
    private var allRecordings: List<DataList> = emptyList()
    private lateinit var adapter: RecordingsAdapter

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        _binding = FragmentLibraryBinding.inflate(inflater, container, false)
        return binding.root
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        adapter = RecordingsAdapter { item ->
            (activity as? com.bayesmech.vision.MainActivity)?.navigateToAnalysis(item)
        }
        binding.recordingsRecyclerView.layoutManager = LinearLayoutManager(requireContext())
        binding.recordingsRecyclerView.adapter = adapter

        binding.searchInput.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
            override fun afterTextChanged(s: Editable?) {
                applyFilter(s?.toString()?.trim() ?: "")
            }
        })

        // Show cached recordings immediately; update list whenever a fetch completes
        viewLifecycleOwner.lifecycleScope.launch {
            viewModel.recordings.collect { recordings ->
                allRecordings = recordings
                applyFilter(binding.searchInput.text?.toString()?.trim() ?: "")
            }
        }

        // Drive the SwipeRefreshLayout spinner from ViewModel state
        viewLifecycleOwner.lifecycleScope.launch {
            viewModel.isRefreshing.collect { refreshing ->
                binding.swipeRefresh.isRefreshing = refreshing
            }
        }

        // Pull-to-refresh triggers a manual reload
        binding.swipeRefresh.setOnRefreshListener {
            viewModel.fetchRecordings()
        }

        // Auto-fetch once on first open
        viewModel.fetchRecordings()
    }

    private fun applyFilter(query: String) {
        val filtered = if (query.isEmpty()) allRecordings
                       else allRecordings.filter { recording ->
                           recording.fileName.contains(query, ignoreCase = true) ||
                               recording.title.contains(query, ignoreCase = true) ||
                               recording.tagsList.any { it.contains(query, ignoreCase = true) }
                       }
        adapter.submitList(filtered)
    }

    override fun onDestroyView() {
        super.onDestroyView()
        binding.swipeRefresh.setOnRefreshListener(null)
        _binding = null
    }
}

class RecordingsAdapter(private val onClick: (DataList) -> Unit) :
    RecyclerView.Adapter<RecordingsAdapter.ViewHolder>() {

    private var items: List<DataList> = emptyList()

    fun submitList(newItems: List<DataList>) {
        items = newItems
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_library_card, parent, false)
        return ViewHolder(view, onClick)
    }

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        holder.bind(items[position])
    }

    override fun getItemCount() = items.size

    class ViewHolder(view: View, val onClick: (DataList) -> Unit) : RecyclerView.ViewHolder(view) {
        private val cardImage: ImageView = view.findViewById(R.id.card_image)
        private val cardProcessingOverlay: FrameLayout = view.findViewById(R.id.card_processing_overlay)
        private val cardTitle: TextView = view.findViewById(R.id.card_title)
        private val cardDate: TextView = view.findViewById(R.id.card_date)
        private val cardPreview: TextView = view.findViewById(R.id.card_preview)
        private val cardTagsRow: LinearLayout = view.findViewById(R.id.card_tags_row)
        private val chatCount: TextView = view.findViewById(R.id.card_chat_count)
        private val rootCard: MaterialCardView = view as MaterialCardView

        fun bind(item: DataList) {
            cardTitle.text = item.title.takeIf { it.isNotBlank() } ?: item.fileName
            cardDate.text = formatRecordingDate(item.fileName)

            // Preview text
            val preview = item.previewText.takeIf { it.isNotBlank() }
            if (preview != null) {
                cardPreview.text = preview
                cardPreview.visibility = View.VISIBLE
            } else {
                cardPreview.visibility = View.GONE
            }

            // Tags as chips — always populated; fall back to #processing when no analysis
            cardTagsRow.removeAllViews()
            val tags = item.tagsList.filter { it.isNotBlank() }
            if (tags.isNotEmpty()) {
                tags.forEach { tag -> addChip(tag, processing = false) }
            } else {
                addChip("#processing", processing = true)
            }

            // Chat count — always shown
            chatCount.text = item.chatMessageCount.toString()

            // Thumbnail + processing overlay
            if (item.imageFrame != null && !item.imageFrame.isEmpty) {
                val bytes = item.imageFrame.toByteArray()
                val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                cardImage.setImageBitmap(bitmap)
            } else {
                cardImage.setImageResource(0)
            }
            cardProcessingOverlay.visibility =
                if (item.isGensparkAvailable) View.GONE else View.VISIBLE

            rootCard.setOnClickListener { onClick(item) }
        }

        private fun addChip(label: String, processing: Boolean) {
            val ctx = itemView.context
            val chip = TextView(ctx).apply {
                text = label
                textSize = 10.5f
                maxLines = 1
                setTextColor(
                    if (processing) ctx.getColor(R.color.nav_icon_inactive)
                    else ctx.getColor(R.color.text_secondary)
                )
                setBackgroundResource(
                    if (processing) R.drawable.tag_chip_processing_background
                    else R.drawable.tag_chip_background
                )
                val ph = dpToPx(8); val pv = dpToPx(3)
                setPadding(ph, pv, ph, pv)
            }
            val params = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            ).apply { marginEnd = dpToPx(6) }
            cardTagsRow.addView(chip, params)
        }

        private fun formatRecordingDate(rawName: String): String {
            return try {
                val parser = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US)
                val formatter = SimpleDateFormat("MMM dd, yyyy · HH:mm", Locale.US)
                formatter.format(parser.parse(rawName)!!)
            } catch (e: Exception) {
                rawName
            }
        }

        private fun dpToPx(dp: Int): Int =
            (dp * itemView.resources.displayMetrics.density).toInt()
    }
}
