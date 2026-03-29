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
import com.google.android.gms.auth.api.signin.GoogleSignIn
import com.bayesmech.vision.DataList
import com.bayesmech.vision.ListRecordingsRequest
import com.bayesmech.vision.ListRecordingsResponse
import com.google.android.material.card.MaterialCardView
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.text.SimpleDateFormat
import java.util.Locale

class LibraryFragment : Fragment() {

    private var _binding: FragmentLibraryBinding? = null
    private val binding get() = _binding!!

    private val viewModel: AppViewModel by activityViewModels()
    private val client = OkHttpClient()
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
                val query = s?.toString()?.trim() ?: ""
                val filtered = if (query.isEmpty()) {
                    allRecordings
                } else {
                    allRecordings.filter { it.fileName.contains(query, ignoreCase = true) }
                }
                adapter.submitList(filtered)
            }
        })

        fetchRecordings()
    }

    private fun fetchRecordings() {
        lifecycleScope.launch(Dispatchers.IO) {
            try {
                // Construct protobuf request using signed-in Google account
                val account = GoogleSignIn.getLastSignedInAccount(requireContext())
                val reqProto = ListRecordingsRequest.newBuilder()
                    .setUsername(account?.displayName ?: account?.email ?: "unknown")
                    .setAuthToken(account?.id ?: "")
                    .build()

                val requestBody = reqProto.toByteArray().toRequestBody("application/x-protobuf".toMediaType())
                val request = Request.Builder()
                    .url(buildInsightgenUrl(viewModel.serverUrl.value))
                    .post(requestBody)
                    .build()

                val response = client.newCall(request).execute()
                if (response.isSuccessful) {
                    val bodyStream = response.body?.byteStream()
                    if (bodyStream != null) {
                        val pbResponse = ListRecordingsResponse.parseFrom(bodyStream)
                        allRecordings = pbResponse.recordingsList
                        withContext(Dispatchers.Main) {
                            adapter.submitList(allRecordings)
                        }
                    }
                }
            } catch (e: Exception) {
                e.printStackTrace()
            }
        }
    }

    private fun buildInsightgenUrl(wsBaseUrl: String): String {
        val httpBase = wsBaseUrl.trimEnd('/')
            .replaceFirst("wss://", "https://")
            .replaceFirst("ws://", "http://")
        return "$httpBase/api/insightgen/recordings"
    }

    override fun onDestroyView() {
        super.onDestroyView()
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
        private val statusPending: TextView = view.findViewById(R.id.card_status_pending)
        private val rootCard: MaterialCardView = view as MaterialCardView

        fun bind(item: DataList) {
            val rawName = item.fileName
            cardTitle.text = try {
                val parser = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US)
                val formatter = SimpleDateFormat("MMM dd, yyyy - HH:mm:ss", Locale.US)
                formatter.format(parser.parse(rawName)!!)
            } catch (e: Exception) {
                rawName
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
    }
}
