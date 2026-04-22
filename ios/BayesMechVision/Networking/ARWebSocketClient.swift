import Foundation
import SwiftProtobuf

final class ARWebSocketClient: NSObject, @unchecked Sendable {
    typealias StatusHandler = @Sendable (ConnectionStatus) -> Void

    var statusHandler: StatusHandler?

    private let session: URLSession
    private let queue = DispatchQueue(label: "com.bayesmech.vision.ios.websocket")
    private var task: URLSessionWebSocketTask?
    private var serverURL: String
    private var status: ConnectionStatus
    private var reconnectWorkItem: DispatchWorkItem?
    private var autoReconnectEnabled = true

    init(serverURL: String) {
        self.serverURL = serverURL
        self.status = .disconnected(serverURL: serverURL)
        let configuration = URLSessionConfiguration.default
        configuration.timeoutIntervalForRequest = 30
        configuration.timeoutIntervalForResource = 30
        self.session = URLSession(configuration: configuration)
        super.init()
    }

    func updateServerURL(_ newValue: String) {
        queue.async {
            self.serverURL = newValue
            self.status.serverURL = newValue
            self.disconnect()
            self.autoReconnectEnabled = true
            self.connect()
        }
    }

    func connect() {
        queue.async {
            self.autoReconnectEnabled = true
            self.connectLocked()
        }
    }

    func disconnect() {
        reconnectWorkItem?.cancel()
        reconnectWorkItem = nil
        autoReconnectEnabled = false
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        status.isConnected = false
        status.isRetrying = false
        status.retryCount = 0
        publishStatus()
    }

    func send(message: PerceiverDataFrame) {
        queue.async {
            do {
                let data = try message.serializedData()
                self.send(data: data)
            } catch {
                self.markFailure(error: error, responseCode: nil)
            }
        }
    }

    func send(data: Data) {
        queue.async {
            guard self.status.isConnected, let task = self.task else { return }
            task.send(.data(data)) { error in
                if let error {
                    self.queue.async {
                        self.markFailure(error: error, responseCode: nil)
                    }
                }
            }
        }
    }

    private func connectLocked() {
        status.connectionAttempts += 1

        guard let url = HTTPBase.streamURL(from: serverURL) else {
            status.lastError = "Invalid URL"
            publishStatus()
            return
        }

        let task = session.webSocketTask(with: url)
        self.task = task
        task.resume()
        receiveNextMessage()

        status.isConnected = true
        status.lastSuccessfulConnection = Date()
        status.serverURL = serverURL
        status.lastError = nil
        status.responseCode = 101
        status.isRetrying = false
        status.retryCount = 0
        publishStatus()
    }

    private func receiveNextMessage() {
        task?.receive { [weak self] result in
            guard let self else { return }
            self.queue.async {
                switch result {
                case .success:
                    self.receiveNextMessage()
                case .failure(let error):
                    self.markFailure(error: error, responseCode: nil)
                }
            }
        }
    }

    private func markFailure(error: Error, responseCode: Int?) {
        status.isConnected = false
        status.lastError = (error as NSError).localizedDescription
        status.lastErrorTime = Date()
        if status.firstFailureTime == nil {
            status.firstFailureTime = Date()
        }
        status.networkError = String(describing: type(of: error))
        status.responseCode = responseCode
        publishStatus()
        scheduleReconnect()
    }

    private func scheduleReconnect() {
        guard autoReconnectEnabled else { return }
        status.retryCount += 1
        let delay = min(0.5 * pow(1.5, Double(status.retryCount)), 5.0)
        status.isRetrying = true
        status.nextRetryInSeconds = delay
        publishStatus()

        reconnectWorkItem?.cancel()
        let workItem = DispatchWorkItem { [weak self] in
            self?.connectLocked()
        }
        reconnectWorkItem = workItem
        queue.asyncAfter(deadline: .now() + delay, execute: workItem)
    }

    private func publishStatus() {
        let snapshot = status
        statusHandler?(snapshot)
    }
}
