import Foundation
import SwiftProtobuf

struct ChatSyncResult {
    var history: ChatHistory?
    var newTurns: [ChatTurn]
}

struct FollowUpResult {
    var sessionID: String?
    var responseText: String
    var userTimestampNs: Int64
    var responseTimestampNs: Int64
}

final class InsightRepository {
    private let session: URLSession
    private let cacheStore: InsightCacheStore

    init(session: URLSession = .shared, cacheStore: InsightCacheStore = InsightCacheStore()) {
        self.session = session
        self.cacheStore = cacheStore
    }

    func listRecordings(serverURL: String, user: SignedInUser?) async throws -> [DataList] {
        guard let url = URL(string: "\(HTTPBase.from(serverURL: serverURL))/api/insightgen/recordings") else {
            throw URLError(.badURL)
        }

        var requestProto = ListRecordingsRequest()
        let username = [user?.displayName, user?.email]
            .compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines) }
            .first(where: { !$0.isEmpty }) ?? "unknown"
        requestProto.username = username
        requestProto.authToken = user?.authToken ?? ""

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/x-protobuf", forHTTPHeaderField: "Content-Type")
        request.httpBody = try requestProto.serializedData()

        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse, (200..<300).contains(httpResponse.statusCode) else {
            throw URLError(.badServerResponse)
        }

        return try ListRecordingsResponse(serializedBytes: data).recordings
    }

    func getSummary(serverURL: String, fileName: String, forceRefresh: Bool = false) async -> GensparkSummary? {
        if !forceRefresh, let cached = cacheStore.readSummary(fileName: fileName) {
            return cached
        }

        guard let url = URL(string: "\(HTTPBase.from(serverURL: serverURL))/api/insightgen/insight?file=\(fileName)") else {
            return cacheStore.readSummary(fileName: fileName)
        }

        do {
            let (data, response) = try await session.data(from: url)
            guard let httpResponse = response as? HTTPURLResponse, (200..<300).contains(httpResponse.statusCode) else {
                return cacheStore.readSummary(fileName: fileName)
            }
            let summary = try GensparkSummary(serializedBytes: data)
            cacheStore.writeSummary(fileName: fileName, summary: summary)
            return summary
        } catch {
            return cacheStore.readSummary(fileName: fileName)
        }
    }

    func getVideoLayer(serverURL: String, fileName: String, layerName: String, forceRefresh: Bool = false) async -> InsightVideoResponse? {
        if !forceRefresh, let cached = cacheStore.readVideoLayer(fileName: fileName, layerName: layerName) {
            return cached
        }

        guard let url = URL(string: "\(HTTPBase.from(serverURL: serverURL))/api/insightgen/video?file=\(fileName)&layer=\(layerName)") else {
            return cacheStore.readVideoLayer(fileName: fileName, layerName: layerName)
        }

        do {
            let (data, response) = try await session.data(from: url)
            guard let httpResponse = response as? HTTPURLResponse, (200..<300).contains(httpResponse.statusCode) else {
                return cacheStore.readVideoLayer(fileName: fileName, layerName: layerName)
            }
            let payload = try InsightVideoResponse(serializedBytes: data)
            cacheStore.writeVideoLayer(fileName: fileName, layerName: layerName, response: payload)
            return payload
        } catch {
            return cacheStore.readVideoLayer(fileName: fileName, layerName: layerName)
        }
    }

    func readCachedChatHistory(fileName: String) -> ChatHistory? {
        cacheStore.readChatHistory(fileName: fileName)
    }

    func syncChatHistory(serverURL: String, fileName: String) async -> ChatSyncResult {
        let sinceTimestamp = cacheStore.latestMessageTimestampNs(fileName: fileName)
        guard let url = URL(string: "\(HTTPBase.from(serverURL: serverURL))/api/insightgen/chat?file=\(fileName)&since_timestamp_ns=\(sinceTimestamp)") else {
            return ChatSyncResult(history: cacheStore.readChatHistory(fileName: fileName), newTurns: [])
        }

        do {
            let (data, response) = try await session.data(from: url)
            guard let httpResponse = response as? HTTPURLResponse, (200..<300).contains(httpResponse.statusCode) else {
                return ChatSyncResult(history: cacheStore.readChatHistory(fileName: fileName), newTurns: [])
            }
            let remoteDelta = try ChatHistory(serializedBytes: data)
            let merged = cacheStore.mergeChatHistory(fileName: fileName, remoteDelta: remoteDelta)
            return ChatSyncResult(history: merged, newTurns: remoteDelta.turns)
        } catch {
            return ChatSyncResult(history: cacheStore.readChatHistory(fileName: fileName), newTurns: [])
        }
    }

    func sendFollowUp(serverURL: String, fileName: String, message: String, sessionID: String?) async -> FollowUpResult? {
        guard let url = URL(string: "\(HTTPBase.from(serverURL: serverURL))/api/insightgen/chat") else {
            return nil
        }

        let payload: [String: Any] = [
            "file": fileName,
            "message": message,
            "session_id": sessionID as Any
        ]

        guard let body = try? JSONSerialization.data(withJSONObject: payload) else { return nil }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = body

        do {
            let (data, response) = try await session.data(for: request)
            guard let httpResponse = response as? HTTPURLResponse, (200..<300).contains(httpResponse.statusCode),
                  let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                return nil
            }

            return FollowUpResult(
                sessionID: (json["session_id"] as? String).flatMap { $0.isEmpty ? sessionID : $0 },
                responseText: json["response"] as? String ?? "",
                userTimestampNs: json["user_timestamp_ns"] as? Int64 ?? Int64((json["user_timestamp_ns"] as? NSNumber)?.int64Value ?? 0),
                responseTimestampNs: json["response_timestamp_ns"] as? Int64 ?? Int64((json["response_timestamp_ns"] as? NSNumber)?.int64Value ?? 0)
            )
        } catch {
            return nil
        }
    }

    func cacheChatExchange(fileName: String, userMessage: String, responseText: String, userTimestampNs: Int64, responseTimestampNs: Int64) {
        var userTurn = ChatTurn()
        userTurn.role = "user"
        userTurn.text = userMessage
        userTurn.timestampNs = userTimestampNs

        var modelTurn = ChatTurn()
        modelTurn.role = "model"
        modelTurn.text = responseText
        modelTurn.timestampNs = responseTimestampNs

        cacheStore.appendChatTurns(fileName: fileName, turns: [userTurn, modelTurn])
    }
}
