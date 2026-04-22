import Foundation
import SwiftProtobuf

final class RecordingManager: @unchecked Sendable {
    private let lock = NSLock()
    private let fileManager: FileManager

    private var fileHandle: FileHandle?
    private var currentFileURL: URL?
    private var lastGoodOffset: UInt64 = 0
    private var frameCount = 0

    init(fileManager: FileManager = .default) {
        self.fileManager = fileManager
    }

    func startRecording() throws -> String {
        lock.lock()
        defer { lock.unlock() }

        let directory = try recordingsDirectory()
        try fileManager.createDirectory(at: directory, withIntermediateDirectories: true, attributes: nil)

        let formatter = DateFormatter()
        formatter.dateFormat = "yyyyMMdd_HHmmss"
        formatter.locale = Locale(identifier: "en_US_POSIX")
        let filename = "arstream_\(formatter.string(from: Date())).pb"
        let url = directory.appendingPathComponent(filename)

        fileManager.createFile(atPath: url.path, contents: nil)
        let handle = try FileHandle(forWritingTo: url)

        fileHandle = handle
        currentFileURL = url
        lastGoodOffset = 0
        frameCount = 0
        return filename
    }

    func stopRecording() throws -> URL? {
        lock.lock()
        defer { lock.unlock() }

        guard let handle = fileHandle else { return nil }
        try handle.truncate(atOffset: lastGoodOffset)
        try handle.synchronize()
        try handle.close()

        let url = currentFileURL
        currentFileURL = nil
        fileHandle = nil
        lastGoodOffset = 0
        frameCount = 0
        return url
    }

    func write(frame: PerceiverDataFrame) throws {
        let data = try frame.serializedData()
        try write(serializedFrame: data)
    }

    func write(serializedFrame data: Data) throws {
        lock.lock()
        defer { lock.unlock() }

        guard let handle = fileHandle else { return }
        var length = UInt32(data.count).bigEndian
        let header = withUnsafeBytes(of: &length) { Data($0) }

        try handle.seekToEnd()
        try handle.write(contentsOf: header)
        try handle.write(contentsOf: data)

        lastGoodOffset += UInt64(header.count + data.count)
        frameCount += 1

        if frameCount % 30 == 0 {
            try handle.synchronize()
        }
    }

    func isRecording() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return fileHandle != nil
    }

    func listRecordings() throws -> [URL] {
        let directory = try recordingsDirectory()
        guard fileManager.fileExists(atPath: directory.path) else { return [] }
        return try fileManager
            .contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
            .filter { $0.lastPathComponent.hasPrefix("arstream_") && $0.pathExtension == "pb" }
            .sorted { $0.lastPathComponent > $1.lastPathComponent }
    }

    private func recordingsDirectory() throws -> URL {
        guard let base = fileManager.urls(for: .documentDirectory, in: .userDomainMask).first else {
            throw NSError(domain: "RecordingManager", code: -1, userInfo: [
                NSLocalizedDescriptionKey: "Could not resolve documents directory"
            ])
        }
        return base.appendingPathComponent("recordings", isDirectory: true)
    }
}
