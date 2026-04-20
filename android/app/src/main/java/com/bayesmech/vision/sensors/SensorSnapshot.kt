package com.bayesmech.vision.sensors

import com.bayesmech.vision.GpsLocation
import com.bayesmech.vision.ImuData
import com.bayesmech.vision.Vector3

data class SensorSnapshot(
    val angularVelocity: FloatArray,
    val magneticField: FloatArray,
    val gravity: FloatArray,
    val linearAcceleration: FloatArray,
    val gpsLocation: GpsLocation?
) {
    fun toImuData(): ImuData {
        val builder = ImuData.newBuilder()
        vectorOrNull(angularVelocity)?.let { builder.angularVelocity = it }
        vectorOrNull(linearAcceleration)?.let { builder.linearAcceleration = it }
        vectorOrNull(gravity)?.let { builder.gravity = it }
        vectorOrNull(magneticField)?.let { builder.magneticField = it }
        return builder.build()
    }

    fun summary(): String {
        val gpsStr = gpsLocation?.let {
            "GPS: %.6f, %.6f".format(it.latitude, it.longitude)
        } ?: "GPS: N/A"
        return "Accel: [%.2f, %.2f, %.2f] m/s², Gyro: [%.2f, %.2f, %.2f] rad/s, Gravity: [%.2f, %.2f, %.2f] m/s², %s".format(
            linearAcceleration[0], linearAcceleration[1], linearAcceleration[2],
            angularVelocity[0], angularVelocity[1], angularVelocity[2],
            gravity[0], gravity[1], gravity[2],
            gpsStr
        )
    }

    private fun vectorOrNull(values: FloatArray): Vector3? {
        if (values.none { it != 0f }) {
            return null
        }
        return Vector3.newBuilder()
            .setX(values[0])
            .setY(values[1])
            .setZ(values[2])
            .build()
    }
}
