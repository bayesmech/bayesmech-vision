package com.bayesmech.vision.ui

import android.graphics.BitmapFactory
import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageView
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
        private val cardTitle: TextView = view.findViewById(R.id.card_title)
        private val cardDate: TextView = view.findViewById(R.id.card_date)
        private val cardTags: TextView = view.findViewById(R.id.card_tags)
        private val statusPending: TextView = view.findViewById(R.id.card_status_pending)
        private val rootCard: MaterialCardView = view as MaterialCardView

        fun bind(item: DataList) {
            val rawName = item.fileName
            cardTitle.text = item.title.takeIf { it.isNotBlank() } ?: fallbackTitle(rawName)
            cardDate.text = formatRecordingDate(rawName)

            val tags = item.tagsList.filter { it.isNotBlank() }
            if (tags.isNotEmpty()) {
                cardTags.text = tags.joinToString(separator = " \u2022 ")
                cardTags.visibility = View.VISIBLE
            } else {
                cardTags.text = ""
                cardTags.visibility = View.GONE
            }

            if (item.imageFrame != null && !item.imageFrame.isEmpty) {
                val bytes = item.imageFrame.toByteArray()
                val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                cardImage.setImageBitmap(bitmap)
            } else {
                cardImage.setImageResource(0)
            }

            val complete = item.isSegmentationAvailable && item.isGensparkAvailable && item.isMotioncapAvailable
            statusPending.visibility = if (complete) View.GONE else View.VISIBLE
            rootCard.alpha = if (complete) 1.0f else 0.55f
            rootCard.setOnClickListener { onClick(item) }
        }

        private fun formatRecordingDate(rawName: String): String {
            return try {
                val parser = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US)
                val formatter = SimpleDateFormat("MMM dd, yyyy - HH:mm:ss", Locale.US)
                formatter.format(parser.parse(rawName)!!)
            } catch (e: Exception) {
                rawName
            }
        }

        private fun fallbackTitle(rawName: String): String {
            val parts = rawName.split("_")
            if (parts.size > 2) {
                return parts.drop(2).joinToString(" ").replaceFirstChar {
                    if (it.isLowerCase()) it.titlecase(Locale.US) else it.toString()
                }
            }
            return rawName
        }
    }
}
