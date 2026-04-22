import Foundation
import SwiftProtobuf

final class InsightCacheStore {
    private let fileManager: FileManager

    init(fileManager: FileManager = .default) {
        self.fileManager = fileManager
    }

    func readSummary(fileName: String) -> GensparkSummary? {
        guard let url = cacheURL(directory: "summary", fileName: fileName, suffix: ".pb"),
              let data = try? Data(contentsOf: url) else {
            return nil
        }
        return try? GensparkSummary(serializedBytes: data)
    }

    func writeSummary(fileName: String, summary: GensparkSummary) {
        guard let url = cacheURL(directory: "summary", fileName: fileName, suffix: ".pb") else { return }
        writeProto(summary, to: url)
    }

    func readVideoLayer(fileName: String, layerName: String) -> InsightVideoResponse? {
        guard let url = cacheURL(directory: "video", fileName: "\(fileName)_\(layerName)", suffix: ".pb"),
              let data = try? Data(contentsOf: url) else {
            return nil
        }
        return try? InsightVideoResponse(serializedBytes: data)
    }

    func writeVideoLayer(fileName: String, layerName: String, response: InsightVideoResponse) {
        guard let url = cacheURL(directory: "video", fileName: "\(fileName)_\(layerName)", suffix: ".pb") else { return }
        writeProto(response, to: url)
    }

    func readChatHistory(fileName: String) -> ChatHistory? {
        guard let url = cacheURL(directory: "chat", fileName: fileName, suffix: ".pb"),
              let data = try? Data(contentsOf: url) else {
            return nil
        }
        return try? ChatHistory(serializedBytes: data)
    }

    func mergeChatHistory(fileName: String, remoteDelta: ChatHistory) -> ChatHistory {
        var merged = readChatHistory(fileName: fileName) ?? ChatHistory()
        if merged.fileName.isEmpty {
            merged.fileName = remoteDelta.fileName
        }
        if remoteDelta.hasInitialTurn {
            merged.initialTurn = remoteDelta.initialTurn
        }
        if !remoteDelta.geminiCacheName.isEmpty {
            merged.geminiCacheName = remoteDelta.geminiCacheName
        }
        if remoteDelta.threadCreatedTimestampNs != 0 {
            merged.threadCreatedTimestampNs = remoteDelta.threadCreatedTimestampNs
        }
        merged.turns.append(contentsOf: remoteDelta.turns)

        if let url = cacheURL(directory: "chat", fileName: fileName, suffix: ".pb") {
            writeProto(merged, to: url)
        }
        return merged
    }

    func appendChatTurns(fileName: String, turns: [ChatTurn]) {
        var history = readChatHistory(fileName: fileName) ?? ChatHistory()
        if history.fileName.isEmpty {
            history.fileName = fileName
        }
        history.turns.append(contentsOf: turns)

        if let url = cacheURL(directory: "chat", fileName: fileName, suffix: ".pb") {
            writeProto(history, to: url)
        }
    }

    func latestMessageTimestampNs(fileName: String) -> Int64 {
        readChatHistory(fileName: fileName)?.turns.last?.timestampNs ?? 0
    }

    private func writeProto<Message: SwiftProtobuf.Message>(_ message: Message, to url: URL) {
        do {
            try fileManager.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true, attributes: nil)
            let data = try message.serializedData()
            try data.write(to: url, options: [.atomic])
        } catch {
            print("Failed to write proto cache \(url.lastPathComponent): \(error)")
        }
    }

    private func cacheURL(directory: String, fileName: String, suffix: String) -> URL? {
        guard let base = fileManager.urls(for: .cachesDirectory, in: .userDomainMask).first else { return nil }
        return base
            .appendingPathComponent("vision_cache", isDirectory: true)
            .appendingPathComponent(directory, isDirectory: true)
            .appendingPathComponent(sanitized(fileName) + suffix)
    }

    private func sanitized(_ value: String) -> String {
        value.replacingOccurrences(of: "/", with: "_")
    }
}
