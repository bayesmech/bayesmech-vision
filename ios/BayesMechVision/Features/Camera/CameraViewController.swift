import ARKit
import AVFoundation
import Combine
import SceneKit
import UIKit

final class CameraViewController: UIViewController {
    var onFullscreenChange: ((Bool) -> Void)?

    private let container: AppContainer
    private let sceneView = ARSCNView()
    private let liveBadge = UILabel()
    private let timerLabel = UILabel()
    private let fullscreenButton = UIButton(type: .system)
    private let chatPanel = UIView()
    private let micButton = UIButton(type: .system)
    private let messageLabel = UILabel()
    private let sendButton = UIButton(type: .system)
    private let recordButton = UIButton(type: .system)

    private var cancellables: Set<AnyCancellable> = []
    private var recordingStartTime: Date?
    private var timer: Timer?
    private var isFullscreen = false
    private var audioRecorder: AVAudioRecorder?
    private var audioFileURL: URL?

    init(container: AppContainer) {
        self.container = container
        super.init(nibName: nil, bundle: nil)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .clear
        buildUI()
        bindState()
        container.arCoordinator.attach(to: sceneView)
    }

    func resumeSessionIfNeeded() {
        container.arCoordinator.attach(to: sceneView)
        container.arCoordinator.start()
    }

    func pauseSession() {
        container.arCoordinator.stop()
    }

    private func buildUI() {
        sceneView.translatesAutoresizingMaskIntoConstraints = false
        chatPanel.translatesAutoresizingMaskIntoConstraints = false
        liveBadge.translatesAutoresizingMaskIntoConstraints = false
        timerLabel.translatesAutoresizingMaskIntoConstraints = false
        fullscreenButton.translatesAutoresizingMaskIntoConstraints = false
        recordButton.translatesAutoresizingMaskIntoConstraints = false
        micButton.translatesAutoresizingMaskIntoConstraints = false
        messageLabel.translatesAutoresizingMaskIntoConstraints = false
        sendButton.translatesAutoresizingMaskIntoConstraints = false

        view.addSubview(sceneView)
        sceneView.pinEdges(to: view)

        liveBadge.text = "● REC"
        liveBadge.textColor = AppColors.textPrimary
        liveBadge.font = .systemFont(ofSize: 12, weight: .bold)
        liveBadge.backgroundColor = AppColors.accentRed
        liveBadge.layer.cornerRadius = 10
        liveBadge.layer.masksToBounds = true
        liveBadge.textAlignment = .center
        liveBadge.isHidden = true

        timerLabel.textColor = AppColors.textPrimary
        timerLabel.font = .monospacedDigitSystemFont(ofSize: 12, weight: .regular)
        timerLabel.backgroundColor = UIColor.black.withAlphaComponent(0.6)
        timerLabel.layer.cornerRadius = 10
        timerLabel.layer.masksToBounds = true
        timerLabel.textAlignment = .center
        timerLabel.isHidden = true

        fullscreenButton.setImage(UIImage(systemName: "arrow.up.left.and.arrow.down.right"), for: .normal)
        fullscreenButton.tintColor = AppColors.textPrimary
        fullscreenButton.backgroundColor = UIColor.black.withAlphaComponent(0.5)
        fullscreenButton.layer.cornerRadius = 16
        fullscreenButton.addTarget(self, action: #selector(toggleFullscreen), for: .touchUpInside)

        chatPanel.backgroundColor = AppColors.bgPanel

        micButton.setImage(UIImage(systemName: "mic.fill"), for: .normal)
        micButton.tintColor = AppColors.textPrimary
        micButton.backgroundColor = AppColors.cardBackground
        micButton.layer.cornerRadius = 32
        micButton.addTarget(self, action: #selector(handleMicTap), for: .touchUpInside)

        messageLabel.text = "Tap the mic to dictate a note"
        messageLabel.textColor = AppColors.textSecondary
        messageLabel.numberOfLines = 2
        messageLabel.font = .systemFont(ofSize: 14, weight: .regular)
        messageLabel.backgroundColor = AppColors.cardBackground
        messageLabel.layer.cornerRadius = 16
        messageLabel.layer.masksToBounds = true
        messageLabel.textAlignment = .left
        messageLabel.layoutMargins = UIEdgeInsets(top: 10, left: 16, bottom: 10, right: 16)

        sendButton.setImage(UIImage(systemName: "paperplane.fill"), for: .normal)
        sendButton.tintColor = AppColors.textPrimary
        sendButton.backgroundColor = AppColors.accentRed
        sendButton.layer.cornerRadius = 28
        sendButton.alpha = 0.4
        sendButton.isEnabled = false
        sendButton.addTarget(self, action: #selector(sendCurrentDraft), for: .touchUpInside)

        recordButton.backgroundColor = AppColors.accentRed
        recordButton.layer.cornerRadius = 40
        recordButton.layer.shadowColor = UIColor.black.cgColor
        recordButton.layer.shadowOpacity = 0.25
        recordButton.layer.shadowRadius = 8
        recordButton.setTitle("REC", for: .normal)
        recordButton.setTitleColor(.white, for: .normal)
        recordButton.titleLabel?.font = .systemFont(ofSize: 16, weight: .bold)
        recordButton.addTarget(self, action: #selector(toggleRecording), for: .touchUpInside)

        view.addSubview(liveBadge)
        view.addSubview(timerLabel)
        view.addSubview(fullscreenButton)
        view.addSubview(chatPanel)
        view.addSubview(recordButton)
        chatPanel.addSubview(micButton)
        chatPanel.addSubview(messageLabel)
        chatPanel.addSubview(sendButton)

        NSLayoutConstraint.activate([
            liveBadge.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 16),
            liveBadge.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 32),
            liveBadge.widthAnchor.constraint(greaterThanOrEqualToConstant: 58),
            liveBadge.heightAnchor.constraint(equalToConstant: 24),

            timerLabel.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 24),
            timerLabel.bottomAnchor.constraint(equalTo: chatPanel.topAnchor, constant: -12),
            timerLabel.widthAnchor.constraint(greaterThanOrEqualToConstant: 68),
            timerLabel.heightAnchor.constraint(equalToConstant: 22),

            fullscreenButton.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -24),
            fullscreenButton.bottomAnchor.constraint(equalTo: chatPanel.topAnchor, constant: -12),
            fullscreenButton.widthAnchor.constraint(equalToConstant: 32),
            fullscreenButton.heightAnchor.constraint(equalToConstant: 32),

            chatPanel.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            chatPanel.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            chatPanel.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            chatPanel.heightAnchor.constraint(equalTo: view.heightAnchor, multiplier: 0.25),

            recordButton.centerXAnchor.constraint(equalTo: chatPanel.centerXAnchor),
            recordButton.centerYAnchor.constraint(equalTo: chatPanel.topAnchor),
            recordButton.widthAnchor.constraint(equalToConstant: 80),
            recordButton.heightAnchor.constraint(equalToConstant: 80),

            micButton.leadingAnchor.constraint(equalTo: chatPanel.leadingAnchor, constant: 16),
            micButton.bottomAnchor.constraint(equalTo: chatPanel.safeAreaLayoutGuide.bottomAnchor, constant: -16),
            micButton.widthAnchor.constraint(equalToConstant: 64),
            micButton.heightAnchor.constraint(equalToConstant: 64),

            sendButton.trailingAnchor.constraint(equalTo: chatPanel.trailingAnchor, constant: -16),
            sendButton.centerYAnchor.constraint(equalTo: micButton.centerYAnchor),
            sendButton.widthAnchor.constraint(equalToConstant: 56),
            sendButton.heightAnchor.constraint(equalToConstant: 56),

            messageLabel.leadingAnchor.constraint(equalTo: micButton.trailingAnchor, constant: 12),
            messageLabel.trailingAnchor.constraint(equalTo: sendButton.leadingAnchor, constant: -12),
            messageLabel.centerYAnchor.constraint(equalTo: micButton.centerYAnchor),
            messageLabel.heightAnchor.constraint(equalToConstant: 56)
        ])
    }

    private func bindState() {
        container.stateStore.$isRecording
            .receive(on: DispatchQueue.main)
            .sink { [weak self] isRecording in
                self?.renderRecordingState(isRecording)
            }
            .store(in: &cancellables)

        Publishers.CombineLatest4(
            container.stateStore.$draftUserText,
            container.stateStore.$isMicRecording,
            container.stateStore.$isTranscribing,
            container.stateStore.$transcriptStatusMessage
        )
        .receive(on: DispatchQueue.main)
        .sink { [weak self] draftText, isMicRecording, isTranscribing, statusMessage in
            self?.renderTranscriptState(
                draftText: draftText,
                isMicRecording: isMicRecording,
                isTranscribing: isTranscribing,
                statusMessage: statusMessage
            )
        }
        .store(in: &cancellables)
    }

    private func renderRecordingState(_ isRecording: Bool) {
        liveBadge.isHidden = !isRecording
        timerLabel.isHidden = !isRecording
        recordButton.backgroundColor = isRecording ? UIColor.white : AppColors.accentRed
        recordButton.setTitleColor(isRecording ? AppColors.accentRed : .white, for: .normal)
        recordButton.setTitle(isRecording ? "STOP" : "REC", for: .normal)

        if isRecording {
            recordingStartTime = Date()
            timer?.invalidate()
            timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
                self?.updateRecordingTimer()
            }
            updateRecordingTimer()
        } else {
            timer?.invalidate()
            timer = nil
            timerLabel.text = nil
        }
    }

    private func renderTranscriptState(draftText: String, isMicRecording: Bool, isTranscribing: Bool, statusMessage: String?) {
        if isMicRecording {
            messageLabel.text = "Listening… tap the mic again to stop"
            messageLabel.textColor = AppColors.textSecondary
            micButton.backgroundColor = AppColors.accentRed
        } else if isTranscribing {
            messageLabel.text = "Transcribing…"
            messageLabel.textColor = AppColors.textSecondary
            micButton.backgroundColor = AppColors.cardBackground
        } else if let statusMessage, !statusMessage.isEmpty {
            messageLabel.text = statusMessage
            messageLabel.textColor = AppColors.textSecondary
            micButton.backgroundColor = AppColors.cardBackground
        } else if !draftText.isEmpty {
            messageLabel.text = draftText
            messageLabel.textColor = AppColors.textPrimary
            micButton.backgroundColor = AppColors.cardBackground
        } else {
            messageLabel.text = "Tap the mic to dictate a note"
            messageLabel.textColor = AppColors.textSecondary
            micButton.backgroundColor = AppColors.cardBackground
        }

        sendButton.isEnabled = !draftText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !isMicRecording && !isTranscribing
        sendButton.alpha = sendButton.isEnabled ? 1.0 : 0.4
    }

    private func updateRecordingTimer() {
        guard let recordingStartTime else { return }
        let elapsed = Int(Date().timeIntervalSince(recordingStartTime))
        timerLabel.text = String(format: " %02d:%02d ", elapsed / 60, elapsed % 60)
    }

    @objc
    private func toggleRecording() {
        if container.stateStore.isRecording {
            _ = try? container.recordingManager.stopRecording()
            container.stateStore.setRecording(false)
        } else {
            do {
                _ = try container.recordingManager.startRecording()
                container.stateStore.setRecording(true)
            } catch {
                container.stateStore.setTranscriptStatusMessage("Failed to start recording: \(error.localizedDescription)")
            }
        }
    }

    @objc
    private func toggleFullscreen() {
        isFullscreen.toggle()
        let imageName = isFullscreen ? "arrow.down.right.and.arrow.up.left" : "arrow.up.left.and.arrow.down.right"
        fullscreenButton.setImage(UIImage(systemName: imageName), for: .normal)
        onFullscreenChange?(isFullscreen)
    }

    @objc
    private func handleMicTap() {
        if container.stateStore.isTranscribing {
            return
        }

        if container.stateStore.isMicRecording {
            stopMicRecordingAndTranscribe()
        } else {
            AVAudioSession.sharedInstance().requestRecordPermission { [weak self] granted in
                DispatchQueue.main.async {
                    guard let self else { return }
                    if granted {
                        self.startMicRecording()
                    } else {
                        self.container.stateStore.setTranscriptStatusMessage("Microphone permission is required for transcription")
                    }
                }
            }
        }
    }

    private func startMicRecording() {
        do {
            let audioSession = AVAudioSession.sharedInstance()
            try audioSession.setCategory(.playAndRecord, mode: .default)
            try audioSession.setActive(true)

            let fileURL = FileManager.default.temporaryDirectory
                .appendingPathComponent("voice_note_\(UUID().uuidString)")
                .appendingPathExtension("m4a")
            audioFileURL = fileURL

            let settings: [String: Any] = [
                AVFormatIDKey: kAudioFormatMPEG4AAC,
                AVSampleRateKey: 44_100,
                AVNumberOfChannelsKey: 1,
                AVEncoderBitRateKey: 128_000
            ]

            audioRecorder = try AVAudioRecorder(url: fileURL, settings: settings)
            audioRecorder?.record()
            container.stateStore.setMicRecording(true)
            container.stateStore.clearTranscriptStatusMessage()
        } catch {
            container.stateStore.setMicRecording(false)
            container.stateStore.setTranscriptStatusMessage("Transcription failed. Try again.")
        }
    }

    private func stopMicRecordingAndTranscribe() {
        audioRecorder?.stop()
        audioRecorder = nil
        container.stateStore.setMicRecording(false)

        guard let audioFileURL else {
            container.stateStore.setTranscriptStatusMessage("Recording was too short to transcribe")
            return
        }

        let attributes = try? FileManager.default.attributesOfItem(atPath: audioFileURL.path)
        let fileSize = (attributes?[.size] as? NSNumber)?.intValue ?? 0
        guard fileSize > 0 else {
            try? FileManager.default.removeItem(at: audioFileURL)
            container.stateStore.setTranscriptStatusMessage("Recording was too short to transcribe")
            return
        }

        container.stateStore.setTranscribing(true)
        container.stateStore.clearTranscriptStatusMessage()

        Task {
            do {
                let transcript = try await container.transcriptionClient.transcribe(
                    audioFileURL: audioFileURL,
                    serverURL: container.stateStore.serverURL
                )
                DispatchQueue.main.async {
                    self.container.stateStore.draftUserText = transcript
                    self.container.stateStore.setTranscribing(false)
                    try? FileManager.default.removeItem(at: audioFileURL)
                }
            } catch {
                DispatchQueue.main.async {
                    self.container.stateStore.setTranscribing(false)
                    self.container.stateStore.setTranscriptStatusMessage("Transcription failed. Try again.")
                    try? FileManager.default.removeItem(at: audioFileURL)
                }
            }
        }
    }

    @objc
    private func sendCurrentDraft() {
        let text = container.stateStore.draftUserText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        container.capturePipeline.sendUserTextInput(text)
        container.stateStore.draftUserText = ""
        container.stateStore.clearTranscriptStatusMessage()
    }
}
