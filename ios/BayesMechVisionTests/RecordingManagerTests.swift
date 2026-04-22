import Foundation
import XCTest
@testable import BayesMechVision

final class RecordingManagerTests: XCTestCase {
    func testRecordingWritesLengthDelimitedFrames() throws {
        let manager = RecordingManager()
        _ = try manager.startRecording()

        var frame = PerceiverDataFrame()
        frame.deviceTimestampNs = 123
        try manager.write(frame: frame)

        guard let fileURL = try manager.stopRecording() else {
            XCTFail("Expected recording URL")
            return
        }

        let bytes = try Data(contentsOf: fileURL)
        XCTAssertGreaterThan(bytes.count, 4)

        let length = bytes.prefix(4).withUnsafeBytes { rawBuffer -> UInt32 in
            rawBuffer.load(as: UInt32.self).bigEndian
        }
        XCTAssertEqual(Int(length), bytes.count - 4)
    }
}
