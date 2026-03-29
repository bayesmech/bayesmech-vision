package com.bayesmech.vision.ui

import android.graphics.BitmapFactory
import android.graphics.Color
import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.activity.OnBackPressedCallback
import androidx.fragment.app.Fragment
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.bayesmech.vision.MainActivity
import com.bayesmech.vision.databinding.FragmentAnalysisBinding
import com.bayesmech.vision.databinding.ItemChatMessageBinding
import com.bayesmech.vision.DataList
import java.text.SimpleDateFormat
import java.util.Locale

data class ChatMessage(val text: String, val isUser: Boolean)

class AnalysisFragment : Fragment() {

    companion object {
        private const val ARG_RECORDING = "recording_bytes"

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

    private lateinit var recording: DataList
    private val messages = mutableListOf<ChatMessage>()
    private lateinit var chatAdapter: ChatAdapter

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

        binding.backButton.setOnClickListener {
            (activity as? MainActivity)?.exitAnalysis()
        }
        populatePreview()
        setupChat()
        setupInput()
    }

    private fun populatePreview() {
        val rawName = recording.fileName
        val parsedDate = try {
            val parser = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US)
            val formatter = SimpleDateFormat("MMM dd, yyyy - HH:mm:ss", Locale.US)
            formatter.format(parser.parse(rawName)!!)
        } catch (e: Exception) {
            rawName
        }
        binding.previewTitle.text = parsedDate

        if (!recording.imageFrame.isEmpty) {
            val bytes = recording.imageFrame.toByteArray()
            val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
            binding.previewImage.setImageBitmap(bitmap)
        }

        binding.previewStatusSeg.setTextColor(
            if (recording.isSegmentationAvailable) Color.GREEN else Color.GRAY
        )
        binding.previewStatusGen.setTextColor(
            if (recording.isGensparkAvailable) Color.GREEN else Color.GRAY
        )
        binding.previewStatusMot.setTextColor(
            if (recording.isMotioncapAvailable) Color.GREEN else Color.GRAY
        )
    }

    private fun setupChat() {
        chatAdapter = ChatAdapter(messages)
        binding.messagesRecyclerView.layoutManager = LinearLayoutManager(requireContext()).apply {
            stackFromEnd = true
        }
        binding.messagesRecyclerView.adapter = chatAdapter
    }

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
            addMessage(ChatMessage(text, isUser = true))
        }
    }

    private fun addMessage(message: ChatMessage) {
        messages.add(message)
        chatAdapter.notifyItemInserted(messages.size - 1)
        binding.messagesRecyclerView.scrollToPosition(messages.size - 1)
    }

    override fun onDestroyView() {
        super.onDestroyView()
        _binding = null
    }
}

class ChatAdapter(private val messages: List<ChatMessage>) :
    RecyclerView.Adapter<ChatAdapter.ViewHolder>() {

    inner class ViewHolder(val binding: ItemChatMessageBinding) :
        RecyclerView.ViewHolder(binding.root)

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val binding = ItemChatMessageBinding.inflate(
            LayoutInflater.from(parent.context), parent, false
        )
        return ViewHolder(binding)
    }

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        val msg = messages[position]
        val tv: TextView = holder.binding.messageText
        tv.text = msg.text

        if (msg.isUser) {
            (holder.itemView as android.widget.LinearLayout).gravity = Gravity.END
            tv.setBackgroundResource(com.bayesmech.vision.R.drawable.bubble_user)
            tv.setTextColor(Color.WHITE)
        } else {
            (holder.itemView as android.widget.LinearLayout).gravity = Gravity.START
            tv.setBackgroundResource(com.bayesmech.vision.R.drawable.bubble_assistant)
            tv.setTextColor(Color.WHITE)
        }
    }

    override fun getItemCount() = messages.size
}
