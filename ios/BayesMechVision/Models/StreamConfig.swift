import Foundation

struct StreamConfig: Sendable {
    var serverURL: String = "ws://192.168.1.100:8080"
    var sendRGBFrames: Bool = true
    var sendDepthFrames: Bool = true
    var enableAdaptiveQuality: Bool = true
}

enum QualityLevel: CaseIterable, Sendable {
    case full
    case high
    case medium
    case low
    case minimal

    var targetFPS: Int {
        switch self {
        case .full, .high:
            return 30
        case .medium:
            return 25
        case .low:
            return 20
        case .minimal:
            return 15
        }
    }

    var jpegQuality: Int {
        switch self {
        case .full:
            return 85
        case .high:
            return 80
        case .medium:
            return 75
        case .low:
            return 70
        case .minimal:
            return 60
        }
    }

    var depthScale: Int {
        switch self {
        case .full, .high, .medium, .low, .minimal:
            return 1
        }
    }

    var sendRGB: Bool { true }

    var sendDepth: Bool {
        switch self {
        case .minimal:
            return false
        default:
            return true
        }
    }
}

final class BandwidthMonitor: @unchecked Sendable {
    private let window: TimeInterval = 5
    private var samples: [(Date, Int)] = []
    private let lock = NSLock()

    func recordSent(bytes: Int) {
        lock.lock()
        defer { lock.unlock() }
        let now = Date()
        samples.append((now, bytes))
        samples.removeAll { now.timeIntervalSince($0.0) > window }
    }

    func currentBandwidthMbps() -> Double {
        lock.lock()
        defer { lock.unlock() }

        guard samples.count >= 2, let first = samples.first?.0, let last = samples.last?.0 else {
            return 0
        }

        let interval = last.timeIntervalSince(first)
        guard interval > 0 else { return 0 }
        let totalBytes = samples.reduce(0) { $0 + $1.1 }
        return (Double(totalBytes) * 8.0) / interval / 1_000_000.0
    }

    func qualityLevel() -> QualityLevel {
        let bandwidth = currentBandwidthMbps()
        switch bandwidth {
        case let value where value > 3.0:
            return .full
        case let value where value > 1.5:
            return .high
        case let value where value > 0.8:
            return .medium
        case let value where value > 0.4:
            return .low
        default:
            return .minimal
        }
    }
}
