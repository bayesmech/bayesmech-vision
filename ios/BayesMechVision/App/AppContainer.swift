import Combine
import Foundation

final class AppContainer {
    let stateStore: AppStateStore
    let coverageTracker: CoverageTracker
    let recordingManager: RecordingManager
    let motionCollector: MotionLocationCollector
    let webSocketClient: ARWebSocketClient
    let transcriptionClient: TranscriptionClient
    let insightRepository: InsightRepository
    let capturePipeline: ARCapturePipeline
    let arCoordinator: ARSessionCoordinator

    private var cancellables: Set<AnyCancellable> = []

    init() {
        let stateStore = AppStateStore()
        let coverageTracker = CoverageTracker()
        let recordingManager = RecordingManager()
        let motionCollector = MotionLocationCollector()
        let webSocketClient = ARWebSocketClient(serverURL: stateStore.serverURL)
        let insightRepository = InsightRepository()
        let capturePipeline = ARCapturePipeline(
            stateStore: stateStore,
            webSocketClient: webSocketClient,
            recordingManager: recordingManager,
            motionCollector: motionCollector,
            coverageTracker: coverageTracker
        )

        self.stateStore = stateStore
        self.coverageTracker = coverageTracker
        self.recordingManager = recordingManager
        self.motionCollector = motionCollector
        self.webSocketClient = webSocketClient
        self.transcriptionClient = TranscriptionClient()
        self.insightRepository = insightRepository
        self.capturePipeline = capturePipeline
        self.arCoordinator = ARSessionCoordinator(stateStore: stateStore, pipeline: capturePipeline)

        wireState()
    }

    func startServices() {
        motionCollector.start()
        webSocketClient.connect()
    }

    func stopServices() {
        motionCollector.stop()
        webSocketClient.disconnect()
    }

    private func wireState() {
        stateStore.$serverURL
            .dropFirst()
            .sink { [weak self] serverURL in
                self?.webSocketClient.updateServerURL(serverURL)
            }
            .store(in: &cancellables)

        stateStore.$enableDepthData
            .merge(with: stateStore.$visualizeDepthMap)
            .sink { [weak self] _ in
                self?.arCoordinator.reconfigureSessionIfNeeded()
            }
            .store(in: &cancellables)

        stateStore.$visualizePointCloud
            .merge(with: stateStore.$visualizePlanes)
            .sink { [weak self] _ in
                self?.arCoordinator.refreshVisualizationOptions()
            }
            .store(in: &cancellables)

        webSocketClient.statusHandler = { [weak self] status in
            Task { @MainActor in
                self?.stateStore.updateConnectionStatus(status)
            }
        }
    }
}
