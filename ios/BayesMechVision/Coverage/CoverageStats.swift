import Foundation

struct CoverageStats: Sendable {
    var depthCoverage: Float = 0
    var accelerometerCoverage: Float = 0
    var gyroscopeCoverage: Float = 0
    var magnetometerCoverage: Float = 0
    var cameraIntrinsicsCount: Int = 0
    var poseCoverage: Float = 0
    var inferredGeometryCoverage: Float = 0
    var gpsCoverage: Float = 0
    var averageFPS: Float = 0
}
