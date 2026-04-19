package com.bayesmech.vision.audio

import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import org.json.JSONObject

class StreamlogTranscriptionClient(
    private val httpClient: OkHttpClient = OkHttpClient()
) {

    suspend fun transcribe(audioFile: File, serverUrl: String): String = withContext(Dispatchers.IO) {
        val httpBase = serverUrl.trim().trimEnd('/')
            .replaceFirst("wss://", "https://")
            .replaceFirst("ws://", "http://")
        require(httpBase.isNotBlank()) { "Server URL is empty" }

        val requestBody = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart(
                name = "file",
                filename = audioFile.name,
                body = audioFile.asRequestBody("audio/mp4".toMediaType())
            )
            .build()

        val request = Request.Builder()
            .url("$httpBase/api/transcribe")
            .post(requestBody)
            .build()

        httpClient.newCall(request).execute().use { response ->
            val responseBody = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                throw IllegalStateException("Streamlog transcription failed: ${response.code} $responseBody")
            }

            val transcript = JSONObject(responseBody).optString("text").trim()
            if (transcript.isEmpty()) {
                throw IllegalStateException("Streamlog transcription returned an empty transcript")
            }
            transcript
        }
    }
}
