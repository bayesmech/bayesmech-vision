import CoreLocation
import CoreMotion
import Foundation
import simd

final class MotionLocationCollector: NSObject, CLLocationManagerDelegate, @unchecked Sendable {
    private let motionManager = CMMotionManager()
    private let locationManager = CLLocationManager()
    private let queue = OperationQueue()
    private let lock = NSLock()

    private var angularVelocity = SIMD3<Float>.zero
    private var magneticField = SIMD3<Float>.zero
    private var gravity = SIMD3<Float>.zero
    private var linearAcceleration = SIMD3<Float>.zero
    private var gpsLocation: GpsLocation?

    override init() {
        super.init()
        locationManager.delegate = self
        locationManager.desiredAccuracy = kCLLocationAccuracyBest
        queue.name = "com.bayesmech.vision.ios.motion"
    }

    func start() {
        DispatchQueue.main.async {
            self.locationManager.requestWhenInUseAuthorization()
            self.locationManager.startUpdatingLocation()
        }

        motionManager.deviceMotionUpdateInterval = 1.0 / 50.0
        motionManager.magnetometerUpdateInterval = 1.0 / 50.0

        if motionManager.isDeviceMotionAvailable {
            motionManager.startDeviceMotionUpdates(to: queue) { [weak self] motion, _ in
                guard let motion, let self else { return }
                self.lock.lock()
                self.angularVelocity = SIMD3<Float>(
                    Float(motion.rotationRate.x),
                    Float(motion.rotationRate.y),
                    Float(motion.rotationRate.z)
                )
                self.gravity = SIMD3<Float>(
                    Float(motion.gravity.x),
                    Float(motion.gravity.y),
                    Float(motion.gravity.z)
                )
                self.linearAcceleration = SIMD3<Float>(
                    Float(motion.userAcceleration.x),
                    Float(motion.userAcceleration.y),
                    Float(motion.userAcceleration.z)
                )
                self.lock.unlock()
            }
        }

        if motionManager.isMagnetometerAvailable {
            motionManager.startMagnetometerUpdates(to: queue) { [weak self] data, _ in
                guard let data, let self else { return }
                self.lock.lock()
                self.magneticField = SIMD3<Float>(
                    Float(data.magneticField.x),
                    Float(data.magneticField.y),
                    Float(data.magneticField.z)
                )
                self.lock.unlock()
            }
        }
    }

    func stop() {
        motionManager.stopDeviceMotionUpdates()
        motionManager.stopMagnetometerUpdates()
        DispatchQueue.main.async {
            self.locationManager.stopUpdatingLocation()
        }
    }

    func currentSnapshot() -> SensorSnapshot {
        lock.lock()
        defer { lock.unlock() }
        return SensorSnapshot(
            angularVelocity: angularVelocity,
            magneticField: magneticField,
            gravity: gravity,
            linearAcceleration: linearAcceleration,
            gpsLocation: gpsLocation
        )
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let location = locations.last else { return }
        var gps = GpsLocation()
        gps.latitude = location.coordinate.latitude
        gps.longitude = location.coordinate.longitude
        gps.altitude = location.altitude
        gps.accuracy = Float(location.horizontalAccuracy)
        gps.bearing = Float(location.course)
        gps.speed = Float(location.speed)
        gps.timestampMs = Int64(location.timestamp.timeIntervalSince1970 * 1000)

        lock.lock()
        gpsLocation = gps
        lock.unlock()
    }
}
