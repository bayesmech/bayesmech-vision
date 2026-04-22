import XCTest
@testable import BayesMechVision

final class CoverageTrackerTests: XCTestCase {
    func testCoverageStartsAtZero() {
        let tracker = CoverageTracker()
        let stats = tracker.stats()

        XCTAssertEqual(stats.depthCoverage, 0)
        XCTAssertEqual(stats.accelerometerCoverage, 0)
        XCTAssertEqual(stats.averageFPS, 0)
    }

    func testCoverageReflectsRecordedFrames() {
        let tracker = CoverageTracker()
        tracker.recordFrame(
            hasDepth: true,
            hasAccelerometer: true,
            hasGyroscope: false,
            hasMagnetometer: true,
            hasIntrinsics: true,
            hasPose: true,
            hasGeometry: false,
            hasGPS: false
        )
        tracker.recordFrame(
            hasDepth: false,
            hasAccelerometer: true,
            hasGyroscope: true,
            hasMagnetometer: false,
            hasIntrinsics: false,
            hasPose: true,
            hasGeometry: true,
            hasGPS: true
        )

        let stats = tracker.stats()
        XCTAssertEqual(stats.depthCoverage, 50, accuracy: 0.1)
        XCTAssertEqual(stats.accelerometerCoverage, 100, accuracy: 0.1)
        XCTAssertEqual(stats.poseCoverage, 100, accuracy: 0.1)
        XCTAssertEqual(stats.gpsCoverage, 50, accuracy: 0.1)
        XCTAssertEqual(stats.cameraIntrinsicsCount, 1)
    }
}
