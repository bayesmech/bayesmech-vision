package com.bayesmech.vision.network

data class StreamConfig(
    val serverUrl: String = "ws://192.168.1.100:8080",

    // Data selection
    val sendRgbFrames: Boolean = true,
    val sendDepthFrames: Boolean = true,

    // Adaptive streaming
    val enableAdaptiveQuality: Boolean = true
)

enum class QualityLevel(
    val targetFps: Int,
    val jpegQuality: Int,
    val depthScale: Float,
    val sendRgb: Boolean,
    val sendDepth: Boolean
) {
    FULL(30, 85, 1.0f, true, true),
    HIGH(30, 80, 1.0f, true, true),
    MEDIUM(25, 75, 1.0f, true, true),
    LOW(20, 70, 1.0f, true, true),
    MINIMAL(15, 60, 1.0f, true, false);
}
