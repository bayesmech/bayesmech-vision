package com.bayesmech.vision.analysis

import android.content.Context
import com.bayesmech.vision.ChatHistory
import com.bayesmech.vision.ChatTurn
import com.bayesmech.vision.GensparkSummary
import com.bayesmech.vision.InsightVideoResponse
import java.io.File

class InsightCacheStore(context: Context) {

    private val rootDir = File(context.applicationContext.filesDir, "analysis_cache").apply {
        mkdirs()
    }

    fun readSummary(fileName: String): GensparkSummary? =
        readProto(summaryFile(fileName), GensparkSummary::parseFrom)

    fun writeSummary(fileName: String, summary: GensparkSummary) {
        writeBytes(summaryFile(fileName), summary.toByteArray())
    }

    fun readVideoLayer(fileName: String, layerName: String): InsightVideoResponse? =
        readProto(videoFile(fileName, layerName), InsightVideoResponse::parseFrom)

    fun writeVideoLayer(fileName: String, layerName: String, response: InsightVideoResponse) {
        writeBytes(videoFile(fileName, layerName), response.toByteArray())
    }

    fun readChatHistory(fileName: String): ChatHistory? =
        readProto(chatFile(fileName), ChatHistory::parseFrom)

    fun mergeChatHistory(fileName: String, delta: ChatHistory): ChatHistory {
        val existing = readChatHistory(fileName)
        val builder = (existing ?: ChatHistory.newBuilder().setFileName(fileName).build()).toBuilder()

        if (delta.fileName.isNotBlank()) {
            builder.fileName = delta.fileName
        }
        if (delta.geminiCacheName.isNotBlank()) {
            builder.geminiCacheName = delta.geminiCacheName
        }
        if (delta.threadCreatedTimestampNs > 0L) {
            builder.threadCreatedTimestampNs = delta.threadCreatedTimestampNs
        }
        if (delta.hasInitialTurn()) {
            builder.initialTurn = delta.initialTurn
        }

        builder.clearTurns()
        builder.addAllTurns(
            mergeTurns(existing?.turnsList.orEmpty(), delta.turnsList)
        )

        return builder.build().also { writeBytes(chatFile(fileName), it.toByteArray()) }
    }

    fun appendChatTurns(
        fileName: String,
        turns: List<ChatTurn>,
        threadCreatedTimestampNs: Long? = null,
    ): ChatHistory {
        if (turns.isEmpty() && threadCreatedTimestampNs == null) {
            return readChatHistory(fileName) ?: ChatHistory.newBuilder().setFileName(fileName).build()
        }

        val delta = ChatHistory.newBuilder()
            .setFileName(fileName)
            .apply {
                threadCreatedTimestampNs?.takeIf { it > 0L }?.let { this.threadCreatedTimestampNs = it }
                addAllTurns(turns)
            }
            .build()
        return mergeChatHistory(fileName, delta)
    }

    fun latestMessageTimestampNs(fileName: String): Long {
        val history = readChatHistory(fileName) ?: return 0L
        var latest = history.threadCreatedTimestampNs
        if (history.hasInitialTurn()) {
            latest = maxOf(latest, history.initialTurn.timestampNs)
        }
        for (turn in history.turnsList) {
            latest = maxOf(latest, turn.timestampNs)
        }
        return latest
    }

    private fun summaryFile(fileName: String): File = File(threadDir(fileName), "summary.pb")

    private fun videoFile(fileName: String, layerName: String): File =
        File(threadDir(fileName), "video_$layerName.pb")

    private fun chatFile(fileName: String): File = File(threadDir(fileName), "chat.pb")

    private fun threadDir(fileName: String): File =
        File(rootDir, sanitize(fileName)).apply { mkdirs() }

    private fun sanitize(fileName: String): String =
        fileName.replace(Regex("[^A-Za-z0-9._-]"), "_")

    private fun writeBytes(file: File, bytes: ByteArray) {
        runCatching {
            file.parentFile?.mkdirs()
            file.writeBytes(bytes)
        }
    }

    private fun <T> readProto(file: File, parser: (ByteArray) -> T): T? {
        return runCatching {
            if (!file.exists()) return null
            parser(file.readBytes())
        }.getOrNull()
    }

    private fun mergeTurns(existing: List<ChatTurn>, incoming: List<ChatTurn>): List<ChatTurn> {
        val merged = linkedMapOf<String, ChatTurn>()
        (existing + incoming)
            .sortedWith(compareBy<ChatTurn> { it.timestampNs }.thenBy { it.role }.thenBy { it.text })
            .forEach { turn ->
                merged[turn.cacheKey()] = turn
            }
        return merged.values.toList()
    }

    private fun ChatTurn.cacheKey(): String = "${timestampNs}|${role}|${text}"
}
