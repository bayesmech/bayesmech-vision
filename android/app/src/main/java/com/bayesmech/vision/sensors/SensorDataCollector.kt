package com.bayesmech.vision.sensors

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Looper
import android.util.Log
import androidx.core.content.ContextCompat
import com.bayesmech.vision.GpsLocation
import com.google.android.gms.location.FusedLocationProviderClient
import com.google.android.gms.location.LocationCallback
import com.google.android.gms.location.LocationRequest
import com.google.android.gms.location.LocationResult
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority
import java.util.concurrent.atomic.AtomicReference

/**
 * Collects real-time sensor data from Android motion sensors and GPS.
 *
 * Manages listeners for:
 * - Gyroscope (angular velocity)
 * - Gravity (gravity vector, OS-fused)
 * - Linear Acceleration (acceleration without gravity, OS-fused)
 * - Magnetometer (raw magnetic field, for server-side orientation fusion)
 * - GPS location (via FusedLocationProviderClient)
 *
 * Thread-safe - sensor values are stored atomically and can be read from any thread.
 */
class SensorDataCollector(private val context: Context) : SensorEventListener {
    private val TAG = "SensorDataCollector"

    private val sensorManager = context.getSystemService(Context.SENSOR_SERVICE) as SensorManager

    // Sensors
    private val gyroscope = sensorManager.getDefaultSensor(Sensor.TYPE_GYROSCOPE)
    private val magnetometer = sensorManager.getDefaultSensor(Sensor.TYPE_MAGNETIC_FIELD)
    private val gravitySensor = sensorManager.getDefaultSensor(Sensor.TYPE_GRAVITY)
    private val linearAccelSensor = sensorManager.getDefaultSensor(Sensor.TYPE_LINEAR_ACCELERATION)

    // Atomic references for thread-safe access
    private val angularVelocity = AtomicReference(FloatArray(3) { 0f })
    private val magneticField = AtomicReference(FloatArray(3) { 0f })
    private val gravity = AtomicReference(FloatArray(3) { 0f })
    private val linearAcceleration = AtomicReference(FloatArray(3) { 0f })

    // GPS
    private var fusedLocationClient: FusedLocationProviderClient? = null
    private val currentLocation = AtomicReference<GpsLocation?>(null)
    private var locationCallback: LocationCallback? = null

    private var isCollecting = false

    /**
     * Start collecting sensor data.
     * Registers listeners at SENSOR_DELAY_GAME rate (~50Hz).
     */
    fun startCollecting() {
        if (isCollecting) {
            Log.w(TAG, "Already collecting sensor data")
            return
        }

        var sensorsRegistered = 0

        sensorsRegistered += registerSensor(gyroscope, "Gyroscope")
        sensorsRegistered += registerSensor(magnetometer, "Magnetometer")
        sensorsRegistered += registerSensor(gravitySensor, "Gravity sensor")
        sensorsRegistered += registerSensor(linearAccelSensor, "Linear acceleration sensor")

        startLocationUpdates()

        isCollecting = true
        Log.i(TAG, "Started collecting from $sensorsRegistered sensors")
    }

    /**
     * Stop collecting sensor data.
     */
    fun stopCollecting() {
        if (!isCollecting) return
        sensorManager.unregisterListener(this)
        stopLocationUpdates()
        isCollecting = false
        Log.i(TAG, "Stopped collecting sensor data")
    }

    override fun onSensorChanged(event: SensorEvent) {
        when (event.sensor.type) {
            Sensor.TYPE_GYROSCOPE -> angularVelocity.set(event.values.clone())
            Sensor.TYPE_MAGNETIC_FIELD -> magneticField.set(event.values.clone())
            Sensor.TYPE_GRAVITY -> gravity.set(event.values.clone())
            Sensor.TYPE_LINEAR_ACCELERATION -> linearAcceleration.set(event.values.clone())
        }
    }

    override fun onAccuracyChanged(sensor: Sensor, accuracy: Int) {
        if (accuracy == SensorManager.SENSOR_STATUS_UNRELIABLE) {
            Log.w(TAG, "Sensor ${sensor.name} accuracy is unreliable")
        }
    }

    // ── GPS / Location ─────────────────────────────────────────────────────

    private fun hasLocationPermission(): Boolean {
        return ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_FINE_LOCATION) ==
                PackageManager.PERMISSION_GRANTED
    }

    private fun startLocationUpdates() {
        if (!hasLocationPermission()) {
            Log.w(TAG, "✗ Location permission not granted — skipping GPS")
            return
        }

        try {
            fusedLocationClient = LocationServices.getFusedLocationProviderClient(context)

            val locationRequest = LocationRequest.Builder(
                Priority.PRIORITY_HIGH_ACCURACY, 1000L
            ).setMinUpdateIntervalMillis(500L).build()

            locationCallback = object : LocationCallback() {
                override fun onLocationResult(result: LocationResult) {
                    val loc = result.lastLocation ?: return
                    val gps = GpsLocation.newBuilder()
                        .setLatitude(loc.latitude)
                        .setLongitude(loc.longitude)
                        .setAltitude(loc.altitude)
                        .setAccuracy(loc.accuracy)
                        .setBearing(loc.bearing)
                        .setSpeed(loc.speed)
                        .setTimestampMs(loc.time)
                        .build()
                    currentLocation.set(gps)
                }
            }

            fusedLocationClient?.requestLocationUpdates(
                locationRequest, locationCallback!!, Looper.getMainLooper()
            )
            Log.i(TAG, "✓ GPS location updates registered")
        } catch (e: SecurityException) {
            Log.e(TAG, "SecurityException requesting location updates", e)
        }
    }

    private fun stopLocationUpdates() {
        locationCallback?.let { cb ->
            fusedLocationClient?.removeLocationUpdates(cb)
        }
        locationCallback = null
        fusedLocationClient = null
    }

    fun getCurrentSnapshot(): SensorSnapshot {
        return SensorSnapshot(
            angularVelocity = angularVelocity.get().clone(),
            magneticField = magneticField.get().clone(),
            gravity = gravity.get().clone(),
            linearAcceleration = linearAcceleration.get().clone(),
            gpsLocation = currentLocation.get()
        )
    }

    private fun registerSensor(sensor: Sensor?, label: String): Int {
        if (sensor == null) {
            Log.w(TAG, "✗ $label not available")
            return 0
        }
        sensorManager.registerListener(this, sensor, SensorManager.SENSOR_DELAY_GAME)
        Log.i(TAG, "✓ $label registered")
        return 1
    }
}
