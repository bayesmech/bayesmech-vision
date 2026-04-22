import ARKit
import Foundation
import UIKit

final class ARCapturePipeline: @unchecked Sendable {
    private let stateStore: AppStateStore
    private let webSocketClient: ARWebSocketClient
    private let recordingManager: RecordingManager
    private let motionCollector: MotionLocationCollector
    private let coverageTracker: CoverageTracker
    private let encoder = FrameEncoder()
    private let bandwidthMonitor = BandwidthMonitor()
    private let queue = DispatchQueue(label: "com.bayesmech.vision.ios.capture", qos: .userInitiated)

    private var currentQuality: QualityLevel = .high
    private var frameNumber = 0
    private var lastSentTimestamp: UInt64 = 0
    private let deviceID = UIDevice.current.identifierForVendor?.uuidString ?? UUID().uuidString

    init(
        stateStore: AppStateStore,
        webSocketClient: ARWebSocketClient,
        recordingManager: RecordingManager,
        motionCollector: MotionLocationCollector,
        coverageTracker: CoverageTracker
    ) {
        self.stateStore = stateStore
        self.webSocketClient = webSocketClient
        self.recordingManager = recordingManager
        self.motionCollector = motionCollector
        self.coverageTracker = coverageTracker
    }

    func process(frame: ARFrame, planeAnchors: [ARPlaneAnchor]) {
        queue.async {
            let now = DispatchTime.now().uptimeNanoseconds
            let minimumInterval = UInt64(1_000_000_000 / self.currentQuality.targetFPS)
            guard now - self.lastSentTimestamp >= minimumInterval else { return }

            self.lastSentTimestamp = now
            let frameNumber = self.frameNumber
            self.frameNumber += 1

            if self.stateStore.enableDepthData || self.stateStore.enableInferredGeometry {
                self.currentQuality = self.bandwidthMonitor.qualityLevel()
            }

            let snapshot = self.motionCollector.currentSnapshot()

            do {
                let payload = try self.encoder.encode(
                    frame: frame,
                    planeAnchors: planeAnchors,
                    sensorSnapshot: snapshot,
                    frameNumber: frameNumber,
                    deviceID: self.deviceID,
                    quality: self.currentQuality,
                    includeRGB: self.currentQuality.sendRGB,
                    includeDepth: self.stateStore.enableDepthData && self.currentQuality.sendDepth,
                    includeGeometry: self.stateStore.enableInferredGeometry
                )

                if self.recordingManager.isRecording() {
                    try? self.recordingManager.write(frame: payload)
                }

                self.webSocketClient.send(message: payload)
                if let data = try? payload.serializedData() {
                    self.bandwidthMonitor.recordSent(bytes: data.count)
                }

                let imu = snapshot.toImuData()
                self.coverageTracker.recordFrame(
                    hasDepth: payload.hasDepthFrame,
                    hasAccelerometer: imu?.hasLinearAcceleration ?? false,
                    hasGyroscope: imu?.hasAngularVelocity ?? false,
                    hasMagnetometer: imu?.hasMagneticField ?? false,
                    hasIntrinsics: payload.hasCameraIntrinsics,
                    hasPose: payload.hasCameraPose,
                    hasGeometry: payload.hasInferredGeometry && (!payload.inferredGeometry.planes.isEmpty || !payload.inferredGeometry.pointCloud.isEmpty),
                    hasGPS: payload.hasGpsLocation
                )

                Task { @MainActor in
                    self.stateStore.updateCoverageStats(self.coverageTracker.stats())
                }
            } catch {
                print("Capture pipeline failed: \(error)")
            }
        }
    }

    func sendUserTextInput(_ text: String) {
        queue.async {
            var payload = PerceiverDataFrame()
            let timestamp = Int64(DispatchTime.now().uptimeNanoseconds)
            var identifier = PerceiverFrameIdentifier()
            identifier.timestampNs = timestamp
            identifier.deviceID = self.deviceID
            payload.deviceTimestampNs = timestamp
            payload.frameIdentifier = identifier
            payload.userTextInput = text
            self.webSocketClient.send(message: payload)
        }
    }
}
