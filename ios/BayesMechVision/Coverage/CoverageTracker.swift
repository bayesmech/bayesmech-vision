import Foundation

final class CoverageTracker: @unchecked Sendable {
    private struct FrameRecord {
        let timestamp: Date
        let hasDepth: Bool
        let hasAccelerometer: Bool
        let hasGyroscope: Bool
        let hasMagnetometer: Bool
        let hasIntrinsics: Bool
        let hasPose: Bool
        let hasGeometry: Bool
        let hasGPS: Bool
    }

    private let lock = NSLock()
    private let window: TimeInterval = 10
    private var frames: [FrameRecord] = []
    private var totalIntrinsicsCount = 0

    func recordFrame(
        hasDepth: Bool,
        hasAccelerometer: Bool,
        hasGyroscope: Bool,
        hasMagnetometer: Bool,
        hasIntrinsics: Bool,
        hasPose: Bool,
        hasGeometry: Bool,
        hasGPS: Bool
    ) {
        lock.lock()
        defer { lock.unlock() }

        if hasIntrinsics {
            totalIntrinsicsCount += 1
        }

        let record = FrameRecord(
            timestamp: Date(),
            hasDepth: hasDepth,
            hasAccelerometer: hasAccelerometer,
            hasGyroscope: hasGyroscope,
            hasMagnetometer: hasMagnetometer,
            hasIntrinsics: hasIntrinsics,
            hasPose: hasPose,
            hasGeometry: hasGeometry,
            hasGPS: hasGPS
        )

        frames.append(record)
        pruneOldFrames()
    }

    func stats() -> CoverageStats {
        lock.lock()
        defer { lock.unlock() }

        pruneOldFrames()
        guard !frames.isEmpty else {
            return CoverageStats(cameraIntrinsicsCount: totalIntrinsicsCount)
        }

        let total = Float(frames.count)
        let span: TimeInterval
        if let first = frames.first?.timestamp, let last = frames.last?.timestamp {
            span = max(last.timeIntervalSince(first), 1.0)
        } else {
            span = 1.0
        }

        return CoverageStats(
            depthCoverage: percentage(\.hasDepth, total: total),
            accelerometerCoverage: percentage(\.hasAccelerometer, total: total),
            gyroscopeCoverage: percentage(\.hasGyroscope, total: total),
            magnetometerCoverage: percentage(\.hasMagnetometer, total: total),
            cameraIntrinsicsCount: totalIntrinsicsCount,
            poseCoverage: percentage(\.hasPose, total: total),
            inferredGeometryCoverage: percentage(\.hasGeometry, total: total),
            gpsCoverage: percentage(\.hasGPS, total: total),
            averageFPS: Float(Double(frames.count) / span)
        )
    }

    func reset() {
        lock.lock()
        defer { lock.unlock() }
        frames.removeAll()
        totalIntrinsicsCount = 0
    }

    private func percentage(_ keyPath: KeyPath<FrameRecord, Bool>, total: Float) -> Float {
        guard total > 0 else { return 0 }
        let count = frames.filter { $0[keyPath: keyPath] }.count
        return Float(count) * 100.0 / total
    }

    private func pruneOldFrames() {
        let cutoff = Date().addingTimeInterval(-window)
        frames.removeAll { $0.timestamp < cutoff }
    }
}
