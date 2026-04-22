import ARKit
import SceneKit
import UIKit

final class ARSessionCoordinator: NSObject, ARSessionDelegate {
    private let stateStore: AppStateStore
    private let pipeline: ARCapturePipeline
    private weak var sceneView: ARSCNView?
    private var planeAnchors: [UUID: ARPlaneAnchor] = [:]
    private var isRunning = false

    init(stateStore: AppStateStore, pipeline: ARCapturePipeline) {
        self.stateStore = stateStore
        self.pipeline = pipeline
        super.init()
    }

    func attach(to sceneView: ARSCNView) {
        self.sceneView = sceneView
        sceneView.session.delegate = self
        sceneView.automaticallyUpdatesLighting = true
        sceneView.backgroundColor = .black
        refreshVisualizationOptions()
    }

    func start() {
        isRunning = true
        reconfigureSessionIfNeeded()
    }

    func stop() {
        isRunning = false
        sceneView?.session.pause()
    }

    func refreshVisualizationOptions() {
        guard let sceneView else { return }
        var debugOptions: ARSCNDebugOptions = []
        if stateStore.visualizePointCloud {
            debugOptions.insert(.showFeaturePoints)
        }
        sceneView.debugOptions = debugOptions
    }

    func reconfigureSessionIfNeeded() {
        guard isRunning, let sceneView else { return }

        let configuration = ARWorldTrackingConfiguration()
        configuration.planeDetection = [.horizontal, .vertical]

        if stateStore.enableDepthData || stateStore.visualizeDepthMap {
            if ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) {
                configuration.frameSemantics.insert(.sceneDepth)
            }
            if ARWorldTrackingConfiguration.supportsFrameSemantics(.smoothedSceneDepth) {
                configuration.frameSemantics.insert(.smoothedSceneDepth)
            }
        }

        sceneView.session.run(configuration, options: [.resetTracking, .removeExistingAnchors])
        refreshVisualizationOptions()
    }

    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        guard frame.camera.trackingState == .normal else { return }
        pipeline.process(frame: frame, planeAnchors: Array(planeAnchors.values))
    }

    func session(_ session: ARSession, didAdd anchors: [ARAnchor]) {
        for anchor in anchors.compactMap({ $0 as? ARPlaneAnchor }) {
            planeAnchors[anchor.identifier] = anchor
        }
    }

    func session(_ session: ARSession, didUpdate anchors: [ARAnchor]) {
        for anchor in anchors.compactMap({ $0 as? ARPlaneAnchor }) {
            planeAnchors[anchor.identifier] = anchor
        }
    }

    func session(_ session: ARSession, didRemove anchors: [ARAnchor]) {
        for anchor in anchors.compactMap({ $0 as? ARPlaneAnchor }) {
            planeAnchors.removeValue(forKey: anchor.identifier)
        }
    }
}
