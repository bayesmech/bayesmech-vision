package com.bayesmech.vision.analysis

import android.content.Context
import com.bayesmech.vision.ChatHistory
import com.bayesmech.vision.ChatTurn
import com.bayesmech.vision.GensparkSummary
import com.bayesmech.vision.InsightVideoResponse
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

data class ChatSyncResult(
    val history: ChatHistory?,
    val newTurns: List<ChatTurn>,
)

data class FollowUpResult(
    val sessionId: String?,
    val responseText: String,
    val userTimestampNs: Long,
    val responseTimestampNs: Long,
)

class InsightRepository(
    context: Context,
    private val httpClient: OkHttpClient = OkHttpClient.Builder()
        .readTimeout(120, TimeUnit.SECONDS)
        .build(),
) {

    private val cacheStore = InsightCacheStore(context)

    suspend fun getSummary(
        serverUrl: String,
        fileName: String,
        forceRefresh: Boolean = false,
    ): GensparkSummary? = withContext(Dispatchers.IO) {
        val cached = cacheStore.readSummary(fileName)
        if (!forceRefresh && cached != null) {
            return@withContext cached
        }

        val remote = runCatching {
            val request = Request.Builder()
                .url("${httpBase(serverUrl)}/api/insightgen/insight?file=$fileName")
                .get()
                .build()
            httpClient.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return@runCatching null
                val bytes = response.body?.bytes() ?: return@runCatching null
                GensparkSummary.parseFrom(bytes)
            }
        }.getOrNull()

        remote?.let { cacheStore.writeSummary(fileName, it) }
        remote ?: cached
    }

    suspend fun getVideoLayer(
        serverUrl: String,
        fileName: String,
        layerName: String,
        forceRefresh: Boolean = false,
    ): InsightVideoResponse? = withContext(Dispatchers.IO) {
        val cached = cacheStore.readVideoLayer(fileName, layerName)
        if (!forceRefresh && cached != null) {
            return@withContext cached
        }

        val remote = runCatching {
            val request = Request.Builder()
                .url("${httpBase(serverUrl)}/api/insightgen/video?file=$fileName&layer=$layerName")
                .get()
                .build()
            httpClient.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return@runCatching null
                val bytes = response.body?.bytes() ?: return@runCatching null
                InsightVideoResponse.parseFrom(bytes)
            }
        }.getOrNull()

        remote?.let { cacheStore.writeVideoLayer(fileName, layerName, it) }
        remote ?: cached
    }

    suspend fun readCachedChatHistory(fileName: String): ChatHistory? = withContext(Dispatchers.IO) {
        cacheStore.readChatHistory(fileName)
    }

    suspend fun syncChatHistory(serverUrl: String, fileName: String): ChatSyncResult =
        withContext(Dispatchers.IO) {
            val cached = cacheStore.readChatHistory(fileName)
            val sinceTimestampNs = cacheStore.latestMessageTimestampNs(fileName)

            val remoteDelta = runCatching {
                val request = Request.Builder()
                    .url("${httpBase(serverUrl)}/api/insightgen/chat?file=$fileName&since_timestamp_ns=$sinceTimestampNs")
                    .get()
                    .build()
                httpClient.newCall(request).execute().use { response ->
                    if (!response.isSuccessful) return@runCatching null
                    val bytes = response.body?.bytes() ?: return@runCatching null
                    ChatHistory.parseFrom(bytes)
                }
            }.getOrNull()

            if (remoteDelta == null) {
                return@withContext ChatSyncResult(history = cached, newTurns = emptyList())
            }

            val merged = cacheStore.mergeChatHistory(fileName, remoteDelta)
            ChatSyncResult(history = merged, newTurns = remoteDelta.turnsList)
        }

    suspend fun sendFollowUp(
        serverUrl: String,
        fileName: String,
        message: String,
        sessionId: String?,
    ): FollowUpResult? = withContext(Dispatchers.IO) {
        runCatching {
            val body = JSONObject().apply {
                put("file", fileName)
                put("message", message)
                sessionId?.let { put("session_id", it) }
            }.toString().toRequestBody("application/json".toMediaType())

            val request = Request.Builder()
                .url("${httpBase(serverUrl)}/api/insightgen/chat")
                .post(body)
                .build()

            httpClient.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return@runCatching null
                val payload = JSONObject(response.body?.string() ?: return@runCatching null)
                FollowUpResult(
                    sessionId = payload.optString("session_id").takeIf { it.isNotBlank() } ?: sessionId,
                    responseText = payload.optString("response"),
                    userTimestampNs = payload.optLong("user_timestamp_ns"),
                    responseTimestampNs = payload.optLong("response_timestamp_ns"),
                )
            }
        }.getOrNull()
    }

    suspend fun cacheChatExchange(
        fileName: String,
        userMessage: String,
        responseText: String,
        userTimestampNs: Long,
        responseTimestampNs: Long,
    ) = withContext(Dispatchers.IO) {
        cacheStore.appendChatTurns(
            fileName = fileName,
            turns = listOf(
                ChatTurn.newBuilder()
                    .setRole("user")
                    .setText(userMessage)
                    .setTimestampNs(userTimestampNs)
                    .build(),
                ChatTurn.newBuilder()
                    .setRole("model")
                    .setText(responseText)
                    .setTimestampNs(responseTimestampNs)
                    .build(),
            ),
        )
    }

    private fun httpBase(serverUrl: String): String =
        serverUrl.trimEnd('/')
            .replaceFirst("wss://", "https://")
            .replaceFirst("ws://", "http://")
}
