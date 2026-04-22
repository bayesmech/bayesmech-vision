import Combine
import Foundation
import SwiftProtobuf

final class AppStateStore: ObservableObject {
    @Published var serverURL: String
    @Published var enableDepthData: Bool
    @Published var enableInferredGeometry: Bool
    @Published var visualizeDepthMap: Bool
    @Published var visualizePointCloud: Bool
    @Published var visualizePlanes: Bool
    @Published private(set) var connectionStatus: ConnectionStatus?
    @Published private(set) var isRecording: Bool = false
    @Published var draftUserText: String = ""
    @Published private(set) var isMicRecording: Bool = false
    @Published private(set) var isTranscribing: Bool = false
    @Published private(set) var transcriptStatusMessage: String?
    @Published private(set) var coverageStats = CoverageStats()
    @Published private(set) var recordings: [DataList] = []
    @Published private(set) var currentUser: SignedInUser?

    private let defaults: UserDefaults
    private let fileManager: FileManager

    init(defaults: UserDefaults = .standard, fileManager: FileManager = .default) {
        self.defaults = defaults
        self.fileManager = fileManager
        self.serverURL = defaults.string(forKey: "server_url") ?? "ws://192.168.1.2:8080"
        self.enableDepthData = defaults.object(forKey: "enable_depth") as? Bool ?? true
        self.enableInferredGeometry = defaults.object(forKey: "enable_geometry") as? Bool ?? false
        self.visualizeDepthMap = defaults.object(forKey: "viz_depth") as? Bool ?? false
        self.visualizePointCloud = defaults.object(forKey: "viz_points") as? Bool ?? true
        self.visualizePlanes = defaults.object(forKey: "viz_planes") as? Bool ?? false
        self.connectionStatus = nil

        loadCurrentUser()
        loadCachedRecordings()
    }

    func setServerURL(_ value: String) {
        serverURL = value
        defaults.set(value, forKey: "server_url")
    }

    func setEnableDepthData(_ value: Bool) {
        enableDepthData = value
        defaults.set(value, forKey: "enable_depth")
    }

    func setEnableInferredGeometry(_ value: Bool) {
        enableInferredGeometry = value
        defaults.set(value, forKey: "enable_geometry")
    }

    func setVisualizeDepthMap(_ value: Bool) {
        visualizeDepthMap = value
        defaults.set(value, forKey: "viz_depth")
    }

    func setVisualizePointCloud(_ value: Bool) {
        visualizePointCloud = value
        defaults.set(value, forKey: "viz_points")
    }

    func setVisualizePlanes(_ value: Bool) {
        visualizePlanes = value
        defaults.set(value, forKey: "viz_planes")
    }

    func updateConnectionStatus(_ status: ConnectionStatus) {
        connectionStatus = status
    }

    func setRecording(_ value: Bool) {
        isRecording = value
    }

    func setMicRecording(_ value: Bool) {
        isMicRecording = value
    }

    func setTranscribing(_ value: Bool) {
        isTranscribing = value
    }

    func setTranscriptStatusMessage(_ message: String?) {
        transcriptStatusMessage = message
    }

    func clearTranscriptStatusMessage() {
        transcriptStatusMessage = nil
    }

    func updateCoverageStats(_ stats: CoverageStats) {
        coverageStats = stats
    }

    func updateRecordings(_ items: [DataList]) {
        recordings = items
        persistRecordings(items)
    }

    func setCurrentUser(_ user: SignedInUser?) {
        currentUser = user
        let key = "signed_in_user"
        if let user {
            let encoder = JSONEncoder()
            if let data = try? encoder.encode(user) {
                defaults.set(data, forKey: key)
            }
        } else {
            defaults.removeObject(forKey: key)
        }
    }

    private func loadCurrentUser() {
        guard let data = defaults.data(forKey: "signed_in_user") else { return }
        currentUser = try? JSONDecoder().decode(SignedInUser.self, from: data)
    }

    private func persistRecordings(_ items: [DataList]) {
        var response = ListRecordingsResponse()
        response.recordings = items
        guard let url = recordingsCacheURL else { return }
        do {
            let data = try response.serializedData()
            try data.write(to: url, options: [.atomic])
        } catch {
            print("Failed to persist recordings cache: \(error)")
        }
    }

    private func loadCachedRecordings() {
        guard let url = recordingsCacheURL, let data = try? Data(contentsOf: url) else { return }
        recordings = (try? ListRecordingsResponse(serializedBytes: data).recordings) ?? []
    }

    private var recordingsCacheURL: URL? {
        let directory = fileManager.urls(for: .documentDirectory, in: .userDomainMask).first
        return directory?.appendingPathComponent("recordings_cache.pb")
    }
}
