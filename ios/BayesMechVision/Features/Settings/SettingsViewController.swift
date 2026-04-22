import Combine
import UIKit

final class SettingsViewController: UIViewController {
    private let container: AppContainer
    private let scrollView = UIScrollView()
    private let stackView = UIStackView()

    private let profileNameLabel = UILabel()
    private let profileEmailLabel = UILabel()
    private let serverURLField = UITextField()
    private let connectionTitleLabel = UILabel()
    private let connectionSubtitleLabel = UILabel()
    private let connectionDot = UIView()
    private let depthSwitch = UISwitch()
    private let geometrySwitch = UISwitch()
    private let pointsSwitch = UISwitch()
    private let planesSwitch = UISwitch()
    private let coverageLabel = UILabel()
    private let savedFilesLabel = UILabel()

    private var cancellables: Set<AnyCancellable> = []

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
        view.backgroundColor = AppColors.bgPanel
        buildUI()
        bindState()
        refreshSavedFiles()
    }

    override func viewWillAppear(_ animated: Bool) {
        super.viewWillAppear(animated)
        refreshSavedFiles()
    }

    private func buildUI() {
        scrollView.translatesAutoresizingMaskIntoConstraints = false
        stackView.translatesAutoresizingMaskIntoConstraints = false
        stackView.axis = .vertical
        stackView.spacing = 16

        view.addSubview(scrollView)
        scrollView.addSubview(stackView)

        NSLayoutConstraint.activate([
            scrollView.topAnchor.constraint(equalTo: view.topAnchor),
            scrollView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            scrollView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            scrollView.bottomAnchor.constraint(equalTo: view.bottomAnchor),

            stackView.topAnchor.constraint(equalTo: scrollView.contentLayoutGuide.topAnchor, constant: 16),
            stackView.leadingAnchor.constraint(equalTo: scrollView.frameLayoutGuide.leadingAnchor, constant: 16),
            stackView.trailingAnchor.constraint(equalTo: scrollView.frameLayoutGuide.trailingAnchor, constant: -16),
            stackView.bottomAnchor.constraint(equalTo: scrollView.contentLayoutGuide.bottomAnchor, constant: -24)
        ])

        stackView.addArrangedSubview(makeProfileCard())
        stackView.addArrangedSubview(makeServerCard())
        stackView.addArrangedSubview(makeSensorCard())
        stackView.addArrangedSubview(makeSavedFilesCard())
        stackView.addArrangedSubview(makeAboutCard())
    }

    private func makeProfileCard() -> UIView {
        let card = cardView()
        let stack = UIStackView()
        stack.axis = .vertical
        stack.spacing = 4
        stack.translatesAutoresizingMaskIntoConstraints = false

        profileNameLabel.font = .systemFont(ofSize: 15, weight: .bold)
        profileNameLabel.textColor = AppColors.textPrimary
        profileEmailLabel.font = .systemFont(ofSize: 13, weight: .regular)
        profileEmailLabel.textColor = AppColors.textSecondary
        stack.addArrangedSubview(profileNameLabel)
        stack.addArrangedSubview(profileEmailLabel)
        card.addSubview(stack)

        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: card.topAnchor, constant: 16),
            stack.leadingAnchor.constraint(equalTo: card.leadingAnchor, constant: 16),
            stack.trailingAnchor.constraint(equalTo: card.trailingAnchor, constant: -16),
            stack.bottomAnchor.constraint(equalTo: card.bottomAnchor, constant: -16)
        ])

        return card
    }

    private func makeServerCard() -> UIView {
        let card = cardView()
        let title = sectionTitle("Server Connection")
        let subtitle = sectionSubtitle("Server URL and connection status")

        serverURLField.translatesAutoresizingMaskIntoConstraints = false
        serverURLField.backgroundColor = AppColors.bgDark
        serverURLField.textColor = AppColors.textPrimary
        serverURLField.layer.cornerRadius = 12
        serverURLField.layer.masksToBounds = true
        serverURLField.font = .systemFont(ofSize: 14, weight: .regular)
        serverURLField.leftView = UIView(frame: CGRect(x: 0, y: 0, width: 12, height: 1))
        serverURLField.leftViewMode = .always
        serverURLField.addTarget(self, action: #selector(serverURLDidChange), for: .editingChanged)

        connectionTitleLabel.font = .systemFont(ofSize: 15, weight: .bold)
        connectionTitleLabel.textColor = AppColors.textPrimary
        connectionSubtitleLabel.font = .systemFont(ofSize: 13, weight: .regular)
        connectionSubtitleLabel.textColor = AppColors.textSecondary

        connectionDot.translatesAutoresizingMaskIntoConstraints = false
        connectionDot.layer.cornerRadius = 6
        connectionDot.layer.masksToBounds = true

        [title, subtitle, serverURLField, connectionTitleLabel, connectionSubtitleLabel, connectionDot].forEach { subview in
            card.addSubview(subview)
        }

        NSLayoutConstraint.activate([
            title.topAnchor.constraint(equalTo: card.topAnchor, constant: 16),
            title.leadingAnchor.constraint(equalTo: card.leadingAnchor, constant: 16),
            title.trailingAnchor.constraint(equalTo: card.trailingAnchor, constant: -16),

            subtitle.topAnchor.constraint(equalTo: title.bottomAnchor, constant: 2),
            subtitle.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            subtitle.trailingAnchor.constraint(equalTo: title.trailingAnchor),

            serverURLField.topAnchor.constraint(equalTo: subtitle.bottomAnchor, constant: 16),
            serverURLField.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            serverURLField.trailingAnchor.constraint(equalTo: title.trailingAnchor),
            serverURLField.heightAnchor.constraint(equalToConstant: 44),

            connectionTitleLabel.topAnchor.constraint(equalTo: serverURLField.bottomAnchor, constant: 16),
            connectionTitleLabel.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            connectionTitleLabel.trailingAnchor.constraint(equalTo: connectionDot.leadingAnchor, constant: -12),

            connectionSubtitleLabel.topAnchor.constraint(equalTo: connectionTitleLabel.bottomAnchor, constant: 2),
            connectionSubtitleLabel.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            connectionSubtitleLabel.trailingAnchor.constraint(equalTo: connectionTitleLabel.trailingAnchor),
            connectionSubtitleLabel.bottomAnchor.constraint(equalTo: card.bottomAnchor, constant: -16),

            connectionDot.centerYAnchor.constraint(equalTo: connectionTitleLabel.centerYAnchor),
            connectionDot.trailingAnchor.constraint(equalTo: title.trailingAnchor),
            connectionDot.widthAnchor.constraint(equalToConstant: 12),
            connectionDot.heightAnchor.constraint(equalToConstant: 12)
        ])

        return card
    }

    private func makeSensorCard() -> UIView {
        let card = cardView()
        let title = sectionTitle("Sensor Properties")
        let subtitle = sectionSubtitle("Frame rates, depth settings, and data coverage")
        let switchesStack = UIStackView()
        switchesStack.axis = .vertical
        switchesStack.spacing = 12
        switchesStack.translatesAutoresizingMaskIntoConstraints = false

        configureSwitch(depthSwitch, action: #selector(depthSwitchChanged))
        configureSwitch(geometrySwitch, action: #selector(geometrySwitchChanged))
        configureSwitch(pointsSwitch, action: #selector(pointsSwitchChanged))
        configureSwitch(planesSwitch, action: #selector(planesSwitchChanged))

        coverageLabel.font = .systemFont(ofSize: 13, weight: .regular)
        coverageLabel.textColor = AppColors.textSecondary
        coverageLabel.numberOfLines = 0
        coverageLabel.translatesAutoresizingMaskIntoConstraints = false

        switchesStack.addArrangedSubview(makeToggleRow(title: "Enable Depth Data", toggle: depthSwitch))
        switchesStack.addArrangedSubview(makeToggleRow(title: "Enable Inferred Geometry", toggle: geometrySwitch))
        switchesStack.addArrangedSubview(makeToggleRow(title: "Visualize Point Cloud", toggle: pointsSwitch))
        switchesStack.addArrangedSubview(makeToggleRow(title: "Visualize Planes", toggle: planesSwitch))

        [title, subtitle, switchesStack, coverageLabel].forEach { subview in
            card.addSubview(subview)
        }

        NSLayoutConstraint.activate([
            title.topAnchor.constraint(equalTo: card.topAnchor, constant: 16),
            title.leadingAnchor.constraint(equalTo: card.leadingAnchor, constant: 16),
            title.trailingAnchor.constraint(equalTo: card.trailingAnchor, constant: -16),

            subtitle.topAnchor.constraint(equalTo: title.bottomAnchor, constant: 2),
            subtitle.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            subtitle.trailingAnchor.constraint(equalTo: title.trailingAnchor),

            switchesStack.topAnchor.constraint(equalTo: subtitle.bottomAnchor, constant: 16),
            switchesStack.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            switchesStack.trailingAnchor.constraint(equalTo: title.trailingAnchor),

            coverageLabel.topAnchor.constraint(equalTo: switchesStack.bottomAnchor, constant: 16),
            coverageLabel.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            coverageLabel.trailingAnchor.constraint(equalTo: title.trailingAnchor),
            coverageLabel.bottomAnchor.constraint(equalTo: card.bottomAnchor, constant: -16)
        ])

        return card
    }

    private func makeSavedFilesCard() -> UIView {
        let card = cardView()
        let title = sectionTitle("Saved Files")
        let subtitle = sectionSubtitle("Locally stored data files")
        savedFilesLabel.font = .systemFont(ofSize: 13, weight: .regular)
        savedFilesLabel.textColor = AppColors.textSecondary
        savedFilesLabel.numberOfLines = 0
        savedFilesLabel.translatesAutoresizingMaskIntoConstraints = false

        [title, subtitle, savedFilesLabel].forEach { subview in
            card.addSubview(subview)
        }
        NSLayoutConstraint.activate([
            title.topAnchor.constraint(equalTo: card.topAnchor, constant: 16),
            title.leadingAnchor.constraint(equalTo: card.leadingAnchor, constant: 16),
            title.trailingAnchor.constraint(equalTo: card.trailingAnchor, constant: -16),

            subtitle.topAnchor.constraint(equalTo: title.bottomAnchor, constant: 2),
            subtitle.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            subtitle.trailingAnchor.constraint(equalTo: title.trailingAnchor),

            savedFilesLabel.topAnchor.constraint(equalTo: subtitle.bottomAnchor, constant: 16),
            savedFilesLabel.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            savedFilesLabel.trailingAnchor.constraint(equalTo: title.trailingAnchor),
            savedFilesLabel.bottomAnchor.constraint(equalTo: card.bottomAnchor, constant: -16)
        ])
        return card
    }

    private func makeAboutCard() -> UIView {
        let card = cardView()
        let title = sectionTitle("About this app")
        let subtitle = sectionSubtitle("App version and developer details")
        let body = UILabel()
        body.translatesAutoresizingMaskIntoConstraints = false
        body.textColor = AppColors.textSecondary
        body.font = .systemFont(ofSize: 13, weight: .regular)
        body.numberOfLines = 0
        body.text = "Bundle ID: com.bayesmech.vision\nNative iOS port scaffold under ios/\nProto and dashboard remain unchanged."
        [title, subtitle, body].forEach { subview in
            card.addSubview(subview)
        }
        NSLayoutConstraint.activate([
            title.topAnchor.constraint(equalTo: card.topAnchor, constant: 16),
            title.leadingAnchor.constraint(equalTo: card.leadingAnchor, constant: 16),
            title.trailingAnchor.constraint(equalTo: card.trailingAnchor, constant: -16),

            subtitle.topAnchor.constraint(equalTo: title.bottomAnchor, constant: 2),
            subtitle.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            subtitle.trailingAnchor.constraint(equalTo: title.trailingAnchor),

            body.topAnchor.constraint(equalTo: subtitle.bottomAnchor, constant: 16),
            body.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            body.trailingAnchor.constraint(equalTo: title.trailingAnchor),
            body.bottomAnchor.constraint(equalTo: card.bottomAnchor, constant: -16)
        ])
        return card
    }

    private func bindState() {
        container.stateStore.$currentUser
            .combineLatest(container.stateStore.$connectionStatus, container.stateStore.$coverageStats)
            .receive(on: DispatchQueue.main)
            .sink { [weak self] user, status, coverage in
                guard let self else { return }
                self.profileNameLabel.text = user?.displayName ?? "Signed Out"
                self.profileEmailLabel.text = user?.email ?? "Tap login to connect a user"
                self.serverURLField.text = self.container.stateStore.serverURL
                self.connectionTitleLabel.text = status?.isConnected == true ? "Connected" : "Disconnected"
                self.connectionSubtitleLabel.text = status?.lastError ?? status?.serverURL ?? "Waiting for connection"
                self.connectionDot.backgroundColor = status?.isConnected == true ? UIColor.systemGreen : UIColor.systemOrange
                self.depthSwitch.isOn = self.container.stateStore.enableDepthData
                self.geometrySwitch.isOn = self.container.stateStore.enableInferredGeometry
                self.pointsSwitch.isOn = self.container.stateStore.visualizePointCloud
                self.planesSwitch.isOn = self.container.stateStore.visualizePlanes
                self.coverageLabel.text = String(
                    format: "Depth %.0f%% · Accel %.0f%% · Gyro %.0f%% · Pose %.0f%% · GPS %.0f%% · FPS %.1f",
                    coverage.depthCoverage,
                    coverage.accelerometerCoverage,
                    coverage.gyroscopeCoverage,
                    coverage.poseCoverage,
                    coverage.gpsCoverage,
                    coverage.averageFPS
                )
            }
            .store(in: &cancellables)
    }

    private func refreshSavedFiles() {
        let fileNames = (try? container.recordingManager.listRecordings().map(\.lastPathComponent)) ?? []
        savedFilesLabel.text = fileNames.isEmpty ? "No local recordings yet." : fileNames.joined(separator: "\n")
    }

    private func cardView() -> UIView {
        let view = UIView()
        view.backgroundColor = AppColors.cardBackground
        view.layer.cornerRadius = 18
        view.translatesAutoresizingMaskIntoConstraints = false
        return view
    }

    private func sectionTitle(_ text: String) -> UILabel {
        let label = UILabel()
        label.translatesAutoresizingMaskIntoConstraints = false
        label.text = text
        label.textColor = AppColors.textPrimary
        label.font = .systemFont(ofSize: 15, weight: .bold)
        return label
    }

    private func sectionSubtitle(_ text: String) -> UILabel {
        let label = UILabel()
        label.translatesAutoresizingMaskIntoConstraints = false
        label.text = text
        label.textColor = AppColors.textSecondary
        label.font = .systemFont(ofSize: 13, weight: .regular)
        label.numberOfLines = 0
        return label
    }

    private func makeToggleRow(title: String, toggle: UISwitch) -> UIView {
        let view = UIView()
        view.translatesAutoresizingMaskIntoConstraints = false
        let label = UILabel()
        label.translatesAutoresizingMaskIntoConstraints = false
        label.text = title
        label.textColor = AppColors.textPrimary
        label.font = .systemFont(ofSize: 14, weight: .regular)
        view.addSubview(label)
        view.addSubview(toggle)

        NSLayoutConstraint.activate([
            label.topAnchor.constraint(equalTo: view.topAnchor),
            label.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            label.bottomAnchor.constraint(equalTo: view.bottomAnchor),

            toggle.leadingAnchor.constraint(greaterThanOrEqualTo: label.trailingAnchor, constant: 12),
            toggle.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            toggle.centerYAnchor.constraint(equalTo: label.centerYAnchor)
        ])
        return view
    }

    private func configureSwitch(_ toggle: UISwitch, action: Selector) {
        toggle.onTintColor = AppColors.accentRed
        toggle.translatesAutoresizingMaskIntoConstraints = false
        toggle.addTarget(self, action: action, for: .valueChanged)
    }

    @objc
    private func serverURLDidChange() {
        container.stateStore.setServerURL(serverURLField.text ?? "")
    }

    @objc
    private func depthSwitchChanged() {
        container.stateStore.setEnableDepthData(depthSwitch.isOn)
    }

    @objc
    private func geometrySwitchChanged() {
        container.stateStore.setEnableInferredGeometry(geometrySwitch.isOn)
    }

    @objc
    private func pointsSwitchChanged() {
        container.stateStore.setVisualizePointCloud(pointsSwitch.isOn)
    }

    @objc
    private func planesSwitchChanged() {
        container.stateStore.setVisualizePlanes(planesSwitch.isOn)
    }
}
