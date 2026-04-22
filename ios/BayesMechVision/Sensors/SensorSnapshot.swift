import Foundation
import simd

struct SensorSnapshot: Sendable {
    var angularVelocity: SIMD3<Float> = .zero
    var magneticField: SIMD3<Float> = .zero
    var gravity: SIMD3<Float> = .zero
    var linearAcceleration: SIMD3<Float> = .zero
    var gpsLocation: GpsLocation?

    func toImuData() -> ImuData? {
        var imu = ImuData()
        var populated = false

        if let angularVelocity = vectorOrNil(angularVelocity) {
            imu.angularVelocity = angularVelocity
            populated = true
        }
        if let linearAcceleration = vectorOrNil(linearAcceleration) {
            imu.linearAcceleration = linearAcceleration
            populated = true
        }
        if let gravity = vectorOrNil(gravity) {
            imu.gravity = gravity
            populated = true
        }
        if let magneticField = vectorOrNil(magneticField) {
            imu.magneticField = magneticField
            populated = true
        }

        return populated ? imu : nil
    }

    func summary() -> String {
        let gps = gpsLocation.map { String(format: "GPS: %.6f, %.6f", $0.latitude, $0.longitude) } ?? "GPS: N/A"
        return String(
            format: "Accel: [%.2f, %.2f, %.2f] m/s², Gyro: [%.2f, %.2f, %.2f] rad/s, Gravity: [%.2f, %.2f, %.2f] m/s², %@",
            linearAcceleration.x, linearAcceleration.y, linearAcceleration.z,
            angularVelocity.x, angularVelocity.y, angularVelocity.z,
            gravity.x, gravity.y, gravity.z,
            gps
        )
    }

    private func vectorOrNil(_ value: SIMD3<Float>) -> Vector3? {
        guard value != .zero else { return nil }
        var vector = Vector3()
        vector.x = value.x
        vector.y = value.y
        vector.z = value.z
        return vector
    }
}
