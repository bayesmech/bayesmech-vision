import UIKit

final class AnalysisViewController: UIViewController {
    private enum VideoLayer: Int {
        case raw = 0
        case understanding = 1

        var layerName: String {
            switch self {
            case .raw:
                return "raw"
            case .understanding:
                return "understanding"
            }
        }
    }

    private let container: AppContainer
    private let recording: DataList

    private let topBar = UIView()
    private let backButton = UIButton(type: .system)
    private let titleLabel = UILabel()
    private let subtitleLabel = UILabel()
    private let tagsLabel = UILabel()
    private let segmentedControl = UISegmentedControl(items: ["Video", "Understanding"])
    private let videoContainer = UIView()
    private let imageView = UIImageView()
    private let videoLoadingLabel = UILabel()
    private let controlsView = UIView()
    private let playPauseButton = UIButton(type: .system)
    private let seekSlider = UISlider()
    private let timeLabel = UILabel()
    private let scrollView = UIScrollView()
    private let contentStack = UIStackView()
    private let summaryTitleLabel = UILabel()
    private let summaryBodyTextView = UITextView()
    private let summaryLoadingLabel = UILabel()
    private let chatStack = UIStackView()
    private let inputContainer = UIView()
    private let messageField = UITextField()
    private let sendButton = UIButton(type: .system)
    private let loadingIndicator = UIActivityIndicatorView(style: .medium)

    private var framesRaw: [Data]?
    private var framesUnderstanding: [Data]?
    private var fps: Float = 15
    private var currentFrameIndex = 0
    private var isPlaying = false
    private var activeLayer: VideoLayer = .raw
    private var playTimer: Timer?
    private var sessionID: String?

    init(container: AppContainer, recording: DataList) {
        self.container = container
        self.recording = recording
        super.init(nibName: nil, bundle: nil)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = AppColors.bgPanel
        buildUI()
        populateHeader()
        fetchSummary()
        fetchVideoLayer(.raw)
        fetchChatHistory()
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        playTimer?.invalidate()
    }

    private func buildUI() {
        [topBar, segmentedControl, videoContainer, controlsView, scrollView, inputContainer].forEach {
            $0.translatesAutoresizingMaskIntoConstraints = false
            view.addSubview($0)
        }

        topBar.backgroundColor = AppColors.bgDark
        videoContainer.backgroundColor = AppColors.bgDark
        controlsView.backgroundColor = AppColors.bgHeader
        inputContainer.backgroundColor = AppColors.cardBackground
        inputContainer.layer.cornerRadius = 20
        inputContainer.layer.masksToBounds = true

        backButton.translatesAutoresizingMaskIntoConstraints = false
        backButton.setImage(UIImage(systemName: "chevron.left"), for: .normal)
        backButton.tintColor = AppColors.textSecondary
        backButton.addTarget(self, action: #selector(handleBack), for: .touchUpInside)

        titleLabel.translatesAutoresizingMaskIntoConstraints = false
        titleLabel.textColor = AppColors.textPrimary
        titleLabel.font = .systemFont(ofSize: 15, weight: .bold)

        subtitleLabel.translatesAutoresizingMaskIntoConstraints = false
        subtitleLabel.textColor = AppColors.textSecondary
        subtitleLabel.font = .systemFont(ofSize: 12, weight: .regular)

        tagsLabel.translatesAutoresizingMaskIntoConstraints = false
        tagsLabel.textColor = AppColors.textSecondary
        tagsLabel.font = .systemFont(ofSize: 12, weight: .regular)
        tagsLabel.numberOfLines = 1

        topBar.addSubview(backButton)
        topBar.addSubview(titleLabel)
        topBar.addSubview(subtitleLabel)
        topBar.addSubview(tagsLabel)

        segmentedControl.selectedSegmentIndex = 0
        segmentedControl.selectedSegmentTintColor = AppColors.cardBackground
        segmentedControl.setTitleTextAttributes([.foregroundColor: AppColors.textSecondary], for: .normal)
        segmentedControl.setTitleTextAttributes([.foregroundColor: AppColors.textPrimary], for: .selected)
        segmentedControl.addTarget(self, action: #selector(videoLayerChanged), for: .valueChanged)

        imageView.translatesAutoresizingMaskIntoConstraints = false
        imageView.contentMode = .scaleAspectFit
        videoLoadingLabel.translatesAutoresizingMaskIntoConstraints = false
        videoLoadingLabel.text = "Loading video…"
        videoLoadingLabel.textColor = AppColors.textSecondary
        videoLoadingLabel.font = .systemFont(ofSize: 13, weight: .regular)
        videoContainer.addSubview(imageView)
        videoContainer.addSubview(videoLoadingLabel)

        playPauseButton.translatesAutoresizingMaskIntoConstraints = false
        playPauseButton.setImage(UIImage(systemName: "play.fill"), for: .normal)
        playPauseButton.tintColor = AppColors.textPrimary
        playPauseButton.addTarget(self, action: #selector(togglePlayback), for: .touchUpInside)

        seekSlider.translatesAutoresizingMaskIntoConstraints = false
        seekSlider.minimumValue = 0
        seekSlider.maximumValue = 100
        seekSlider.minimumTrackTintColor = AppColors.textPrimary
        seekSlider.maximumTrackTintColor = AppColors.textSecondary
        seekSlider.addTarget(self, action: #selector(seekChanged), for: .valueChanged)

        timeLabel.translatesAutoresizingMaskIntoConstraints = false
        timeLabel.textColor = AppColors.textSecondary
        timeLabel.font = .monospacedDigitSystemFont(ofSize: 12, weight: .regular)
        timeLabel.text = "0:00 / 0:00"

        controlsView.addSubview(playPauseButton)
        controlsView.addSubview(seekSlider)
        controlsView.addSubview(timeLabel)

        scrollView.alwaysBounceVertical = true
        contentStack.translatesAutoresizingMaskIntoConstraints = false
        contentStack.axis = .vertical
        contentStack.spacing = 16
        scrollView.addSubview(contentStack)

        summaryTitleLabel.textColor = AppColors.textPrimary
        summaryTitleLabel.font = .systemFont(ofSize: 20, weight: .bold)
        summaryTitleLabel.numberOfLines = 0
        summaryTitleLabel.isHidden = true

        summaryBodyTextView.backgroundColor = .clear
        summaryBodyTextView.textColor = AppColors.textPrimary
        summaryBodyTextView.font = .systemFont(ofSize: 15, weight: .regular)
        summaryBodyTextView.isEditable = false
        summaryBodyTextView.isScrollEnabled = false
        summaryBodyTextView.textContainerInset = .zero
        summaryBodyTextView.textContainer.lineFragmentPadding = 0
        summaryBodyTextView.isHidden = true

        summaryLoadingLabel.textColor = AppColors.textSecondary
        summaryLoadingLabel.font = .systemFont(ofSize: 14, weight: .regular)
        summaryLoadingLabel.textAlignment = .center
        summaryLoadingLabel.text = "Loading analysis…"

        chatStack.axis = .vertical
        chatStack.spacing = 8
        chatStack.isHidden = true

        [summaryTitleLabel, summaryBodyTextView, summaryLoadingLabel, chatStack].forEach {
            contentStack.addArrangedSubview($0)
        }

        messageField.translatesAutoresizingMaskIntoConstraints = false
        messageField.backgroundColor = .clear
        messageField.textColor = AppColors.textPrimary
        messageField.attributedPlaceholder = NSAttributedString(
            string: "Message…",
            attributes: [.foregroundColor: AppColors.textSecondary]
        )
        messageField.addTarget(self, action: #selector(messageFieldChanged), for: .editingChanged)

        sendButton.translatesAutoresizingMaskIntoConstraints = false
        sendButton.setImage(UIImage(systemName: "paperplane.fill"), for: .normal)
        sendButton.tintColor = AppColors.textPrimary
        sendButton.isEnabled = false
        sendButton.alpha = 0.35
        sendButton.addTarget(self, action: #selector(handleSend), for: .touchUpInside)

        loadingIndicator.translatesAutoresizingMaskIntoConstraints = false
        loadingIndicator.color = AppColors.textPrimary
        loadingIndicator.isHidden = true

        inputContainer.addSubview(messageField)
        inputContainer.addSubview(sendButton)
        inputContainer.addSubview(loadingIndicator)

        let safe = view.safeAreaLayoutGuide
        NSLayoutConstraint.activate([
            topBar.topAnchor.constraint(equalTo: view.topAnchor),
            topBar.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            topBar.trailingAnchor.constraint(equalTo: view.trailingAnchor),

            backButton.leadingAnchor.constraint(equalTo: topBar.leadingAnchor, constant: 4),
            backButton.topAnchor.constraint(equalTo: safe.topAnchor, constant: 6),
            backButton.widthAnchor.constraint(equalToConstant: 44),
            backButton.heightAnchor.constraint(equalToConstant: 44),

            titleLabel.topAnchor.constraint(equalTo: safe.topAnchor, constant: 10),
            titleLabel.leadingAnchor.constraint(equalTo: backButton.trailingAnchor, constant: 2),
            titleLabel.trailingAnchor.constraint(equalTo: topBar.trailingAnchor, constant: -16),

            subtitleLabel.topAnchor.constraint(equalTo: titleLabel.bottomAnchor, constant: 3),
            subtitleLabel.leadingAnchor.constraint(equalTo: titleLabel.leadingAnchor),
            subtitleLabel.trailingAnchor.constraint(equalTo: titleLabel.trailingAnchor),

            tagsLabel.topAnchor.constraint(equalTo: subtitleLabel.bottomAnchor, constant: 3),
            tagsLabel.leadingAnchor.constraint(equalTo: titleLabel.leadingAnchor),
            tagsLabel.trailingAnchor.constraint(equalTo: titleLabel.trailingAnchor),
            tagsLabel.bottomAnchor.constraint(equalTo: topBar.bottomAnchor, constant: -8),

            segmentedControl.topAnchor.constraint(equalTo: topBar.bottomAnchor, constant: 12),
            segmentedControl.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 16),
            segmentedControl.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -16),

            videoContainer.topAnchor.constraint(equalTo: segmentedControl.bottomAnchor, constant: 12),
            videoContainer.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            videoContainer.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            videoContainer.heightAnchor.constraint(equalToConstant: 220),

            imageView.topAnchor.constraint(equalTo: videoContainer.topAnchor),
            imageView.leadingAnchor.constraint(equalTo: videoContainer.leadingAnchor),
            imageView.trailingAnchor.constraint(equalTo: videoContainer.trailingAnchor),
            imageView.bottomAnchor.constraint(equalTo: videoContainer.bottomAnchor),

            videoLoadingLabel.centerXAnchor.constraint(equalTo: videoContainer.centerXAnchor),
            videoLoadingLabel.centerYAnchor.constraint(equalTo: videoContainer.centerYAnchor),

            controlsView.topAnchor.constraint(equalTo: videoContainer.bottomAnchor),
            controlsView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            controlsView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            controlsView.heightAnchor.constraint(equalToConstant: 44),

            playPauseButton.leadingAnchor.constraint(equalTo: controlsView.leadingAnchor, constant: 8),
            playPauseButton.centerYAnchor.constraint(equalTo: controlsView.centerYAnchor),
            playPauseButton.widthAnchor.constraint(equalToConstant: 36),
            playPauseButton.heightAnchor.constraint(equalToConstant: 36),

            seekSlider.leadingAnchor.constraint(equalTo: playPauseButton.trailingAnchor, constant: 6),
            seekSlider.centerYAnchor.constraint(equalTo: controlsView.centerYAnchor),

            timeLabel.leadingAnchor.constraint(equalTo: seekSlider.trailingAnchor, constant: 8),
            timeLabel.trailingAnchor.constraint(equalTo: controlsView.trailingAnchor, constant: -8),
            timeLabel.centerYAnchor.constraint(equalTo: controlsView.centerYAnchor),

            scrollView.topAnchor.constraint(equalTo: controlsView.bottomAnchor),
            scrollView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            scrollView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            scrollView.bottomAnchor.constraint(equalTo: inputContainer.topAnchor, constant: -12),

            contentStack.topAnchor.constraint(equalTo: scrollView.contentLayoutGuide.topAnchor, constant: 16),
            contentStack.leadingAnchor.constraint(equalTo: scrollView.frameLayoutGuide.leadingAnchor, constant: 20),
            contentStack.trailingAnchor.constraint(equalTo: scrollView.frameLayoutGuide.trailingAnchor, constant: -20),
            contentStack.bottomAnchor.constraint(equalTo: scrollView.contentLayoutGuide.bottomAnchor, constant: -16),

            inputContainer.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 16),
            inputContainer.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -16),
            inputContainer.bottomAnchor.constraint(equalTo: safe.bottomAnchor, constant: -12),
            inputContainer.heightAnchor.constraint(greaterThanOrEqualToConstant: 52),

            messageField.leadingAnchor.constraint(equalTo: inputContainer.leadingAnchor, constant: 14),
            messageField.centerYAnchor.constraint(equalTo: inputContainer.centerYAnchor),
            messageField.trailingAnchor.constraint(equalTo: loadingIndicator.leadingAnchor, constant: -8),

            loadingIndicator.centerYAnchor.constraint(equalTo: inputContainer.centerYAnchor),
            loadingIndicator.trailingAnchor.constraint(equalTo: sendButton.leadingAnchor, constant: -8),

            sendButton.trailingAnchor.constraint(equalTo: inputContainer.trailingAnchor, constant: -10),
            sendButton.centerYAnchor.constraint(equalTo: inputContainer.centerYAnchor),
            sendButton.widthAnchor.constraint(equalToConstant: 36),
            sendButton.heightAnchor.constraint(equalToConstant: 36)
        ])
    }

    private func populateHeader() {
        titleLabel.text = recording.title.isEmpty ? recording.fileName : recording.title

        let formatter = DateFormatter()
        formatter.dateFormat = "yyyyMMdd_HHmmss"
        formatter.locale = Locale(identifier: "en_US_POSIX")
        if let date = formatter.date(from: recording.fileName) {
            let display = DateFormatter()
            display.dateFormat = "MMM dd, yyyy · HH:mm"
            subtitleLabel.text = display.string(from: date)
        } else {
            subtitleLabel.text = recording.fileName
        }

        tagsLabel.text = recording.tags.joined(separator: " • ")
    }

    private func fetchSummary() {
        Task {
            let summary = await container.insightRepository.getSummary(
                serverURL: container.stateStore.serverURL,
                fileName: recording.fileName
            )

            DispatchQueue.main.async {
                if let summary, (!summary.title.isEmpty || !summary.text.isEmpty || !summary.parameters.isEmpty) {
                    self.summaryTitleLabel.isHidden = summary.title.isEmpty
                    self.summaryTitleLabel.text = summary.title
                    self.summaryBodyTextView.isHidden = false
                    self.summaryBodyTextView.text = self.renderSummaryBody(summary)
                    self.summaryLoadingLabel.isHidden = true
                } else if let history = self.container.insightRepository.readCachedChatHistory(fileName: self.recording.fileName), history.hasInitialTurn {
                    self.summaryTitleLabel.isHidden = true
                    self.summaryBodyTextView.isHidden = false
                    self.summaryBodyTextView.text = history.initialTurn.text
                    self.summaryLoadingLabel.isHidden = true
                } else {
                    self.summaryLoadingLabel.text = "No analysis available yet."
                }
            }
        }
    }

    private func renderSummaryBody(_ summary: GensparkSummary) -> String {
        var lines: [String] = []
        if !summary.text.isEmpty {
            lines.append(summary.text)
        }
        if !summary.parameters.isEmpty {
            lines.append("")
            for parameter in summary.parameters {
                let unit = parameter.unit.isEmpty ? "" : " \(parameter.unit)"
                lines.append("• \(parameter.name): \(parameter.value)\(unit)")
            }
        }
        return lines.joined(separator: "\n")
    }

    private func fetchVideoLayer(_ layer: VideoLayer) {
        if layer == .understanding {
            videoLoadingLabel.text = "Loading understanding…"
        } else {
            videoLoadingLabel.text = "Loading video…"
        }
        videoLoadingLabel.isHidden = false

        Task {
            let response = await container.insightRepository.getVideoLayer(
                serverURL: container.stateStore.serverURL,
                fileName: recording.fileName,
                layerName: layer.layerName
            )

            DispatchQueue.main.async {
                guard let response, !response.frames.isEmpty else {
                    self.videoLoadingLabel.text = layer == .understanding ? "No motion capture available." : "Video unavailable."
                    return
                }

                let frames = response.frames.map(\.jpegData)
                if layer == .raw {
                    self.framesRaw = frames
                    self.fps = response.fps > 0 ? response.fps : 15
                } else {
                    self.framesUnderstanding = frames
                }

                if self.activeLayer == layer {
                    self.videoLoadingLabel.isHidden = true
                    let clampedIndex = min(self.currentFrameIndex, frames.count - 1)
                    self.advanceFrame(to: clampedIndex)
                }
            }
        }
    }

    private func fetchChatHistory() {
        Task {
            let result = await container.insightRepository.syncChatHistory(
                serverURL: container.stateStore.serverURL,
                fileName: recording.fileName
            )
            DispatchQueue.main.async {
                self.renderChatTurns(result.history?.turns ?? [])
            }
        }
    }

    private func renderChatTurns(_ turns: [ChatTurn]) {
        guard !turns.isEmpty else { return }
        chatStack.isHidden = false
        chatStack.arrangedSubviews.forEach { view in
            chatStack.removeArrangedSubview(view)
            view.removeFromSuperview()
        }
        turns.forEach { turn in
            addChatBubble(text: turn.text, isUser: turn.role == "user")
        }
    }

    private func addChatBubble(text: String, isUser: Bool) {
        let containerView = UIView()
        let label = UILabel()
        label.translatesAutoresizingMaskIntoConstraints = false
        label.numberOfLines = 0
        label.text = text
        label.textColor = .white
        label.font = .systemFont(ofSize: 15, weight: .regular)
        label.backgroundColor = isUser ? AppColors.accentRed : AppColors.cardBackground
        label.layer.cornerRadius = 16
        label.layer.masksToBounds = true
        label.setContentCompressionResistancePriority(.required, for: .vertical)
        containerView.addSubview(label)

        let leading = isUser ? label.leadingAnchor.constraint(greaterThanOrEqualTo: containerView.leadingAnchor, constant: 60) : label.leadingAnchor.constraint(equalTo: containerView.leadingAnchor)
        let trailing = isUser ? label.trailingAnchor.constraint(equalTo: containerView.trailingAnchor) : label.trailingAnchor.constraint(lessThanOrEqualTo: containerView.trailingAnchor, constant: -60)

        NSLayoutConstraint.activate([
            label.topAnchor.constraint(equalTo: containerView.topAnchor),
            leading,
            trailing,
            label.bottomAnchor.constraint(equalTo: containerView.bottomAnchor)
        ])

        chatStack.addArrangedSubview(containerView)
        scrollToBottom()
    }

    private func scrollToBottom() {
        view.layoutIfNeeded()
        let bottom = CGPoint(x: 0, y: max(0, scrollView.contentSize.height - scrollView.bounds.height))
        scrollView.setContentOffset(bottom, animated: true)
    }

    @objc
    private func handleBack() {
        navigationController?.popViewController(animated: true)
    }

    @objc
    private func videoLayerChanged() {
        activeLayer = VideoLayer(rawValue: segmentedControl.selectedSegmentIndex) ?? .raw
        if activeFrames == nil {
            fetchVideoLayer(activeLayer)
        } else {
            advanceFrame(to: min(currentFrameIndex, max((activeFrames?.count ?? 1) - 1, 0)))
        }
    }

    @objc
    private func togglePlayback() {
        isPlaying.toggle()
        playPauseButton.setImage(UIImage(systemName: isPlaying ? "pause.fill" : "play.fill"), for: .normal)
        playTimer?.invalidate()
        guard isPlaying else { return }
        playTimer = Timer.scheduledTimer(withTimeInterval: TimeInterval(1.0 / max(fps, 1)), repeats: true) { [weak self] _ in
            guard let self, let frames = self.activeFrames, !frames.isEmpty else { return }
            if self.currentFrameIndex < frames.count - 1 {
                self.advanceFrame(to: self.currentFrameIndex + 1)
            } else {
                self.isPlaying = false
                self.playPauseButton.setImage(UIImage(systemName: "play.fill"), for: .normal)
                self.playTimer?.invalidate()
            }
        }
    }

    @objc
    private func seekChanged() {
        guard let frames = activeFrames, !frames.isEmpty else { return }
        let index = Int((seekSlider.value / 100.0) * Float(max(frames.count - 1, 0)))
        advanceFrame(to: index)
    }

    private func advanceFrame(to index: Int) {
        currentFrameIndex = index
        guard let frames = activeFrames, frames.indices.contains(index) else { return }
        imageView.image = UIImage(data: frames[index])
        seekSlider.value = frames.count <= 1 ? 0 : (Float(index) / Float(frames.count - 1)) * 100
        let seconds = fps > 0 ? Float(index) / fps : 0
        let total = fps > 0 ? Float(max(frames.count - 1, 0)) / fps : 0
        timeLabel.text = "\(formatTime(seconds)) / \(formatTime(total))"
    }

    private var activeFrames: [Data]? {
        switch activeLayer {
        case .raw:
            return framesRaw
        case .understanding:
            return framesUnderstanding
        }
    }

    private func formatTime(_ seconds: Float) -> String {
        let value = Int(seconds)
        return "\(value / 60):" + String(format: "%02d", value % 60)
    }

    @objc
    private func messageFieldChanged() {
        let hasText = !(messageField.text?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ?? true)
        sendButton.isEnabled = hasText
        sendButton.alpha = hasText ? 1.0 : 0.35
    }

    @objc
    private func handleSend() {
        let text = messageField.text?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        guard !text.isEmpty else { return }

        messageField.text = nil
        messageFieldChanged()
        addChatBubble(text: text, isUser: true)
        loadingIndicator.isHidden = false
        loadingIndicator.startAnimating()

        Task {
            let result = await container.insightRepository.sendFollowUp(
                serverURL: container.stateStore.serverURL,
                fileName: recording.fileName,
                message: text,
                sessionID: sessionID
            )

            DispatchQueue.main.async {
                self.loadingIndicator.stopAnimating()
                self.loadingIndicator.isHidden = true

                guard let result else {
                    self.addChatBubble(text: "Follow-up failed. Please try again.", isUser: false)
                    return
                }

                self.sessionID = result.sessionID
                self.addChatBubble(text: result.responseText, isUser: false)
                self.container.insightRepository.cacheChatExchange(
                    fileName: self.recording.fileName,
                    userMessage: text,
                    responseText: result.responseText,
                    userTimestampNs: result.userTimestampNs,
                    responseTimestampNs: result.responseTimestampNs
                )
            }
        }
    }
}
