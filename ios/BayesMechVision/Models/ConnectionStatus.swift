import Foundation

struct ConnectionStatus: Sendable {
    var isConnected: Bool
    var lastError: String?
    var lastErrorTime: Date?
    var firstFailureTime: Date?
    var connectionAttempts: Int
    var lastSuccessfulConnection: Date?
    var serverURL: String
    var responseCode: Int?
    var networkError: String?
    var isRetrying: Bool
    var retryCount: Int
    var nextRetryInSeconds: TimeInterval?

    static func disconnected(serverURL: String) -> ConnectionStatus {
        ConnectionStatus(
            isConnected: false,
            lastError: nil,
            lastErrorTime: nil,
            firstFailureTime: nil,
            connectionAttempts: 0,
            lastSuccessfulConnection: nil,
            serverURL: serverURL,
            responseCode: nil,
            networkError: nil,
            isRetrying: false,
            retryCount: 0,
            nextRetryInSeconds: nil
        )
    }
}
