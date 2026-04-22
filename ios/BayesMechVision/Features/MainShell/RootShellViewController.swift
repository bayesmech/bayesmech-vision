import UIKit

final class RootShellViewController: UIViewController {
    private enum Tab: Int {
        case camera = 0
        case library = 1
        case settings = 2
    }

    private let container: AppContainer
    private let headerView = UIView()
    private let contentContainer = UIView()
    private let bottomBar = UIView()
    private let navButtons = UIStackView()
    private let indicatorStack = UIStackView()
    private let titleLabel = UILabel()
    private let logoView = UIImageView()
    private let headerDivider = UIView()
    private let navDivider = UIView()

    private let cameraButton = UIButton(type: .system)
    private let libraryButton = UIButton(type: .system)
    private let settingsButton = UIButton(type: .system)

    private let cameraIndicator = UIView()
    private let libraryIndicator = UIView()
    private let settingsIndicator = UIView()

    private lazy var cameraViewController: CameraViewController = {
        let controller = CameraViewController(container: container)
        controller.onFullscreenChange = { [weak self] hidden in
            self?.setChromeHidden(hidden)
        }
        return controller
    }()

    private lazy var libraryViewController: LibraryViewController = {
        let controller = LibraryViewController(container: container)
        controller.onSelectRecording = { [weak self] recording in
            self?.showAnalysis(recording: recording)
        }
        return controller
    }()

    private lazy var settingsViewController = SettingsViewController(container: container)

    private var selectedTab: Tab = .camera
    private var currentChild: UIViewController?

    init(container: AppContainer) {
        self.container = container
        super.init(nibName: nil, bundle: nil)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    deinit {
        container.stopServices()
    }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = AppColors.bgDark
        buildUI()
        switchToTab(.camera)
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        container.startServices()
        cameraViewController.resumeSessionIfNeeded()
    }

    private func buildUI() {
        headerView.translatesAutoresizingMaskIntoConstraints = false
        contentContainer.translatesAutoresizingMaskIntoConstraints = false
        bottomBar.translatesAutoresizingMaskIntoConstraints = false
        navButtons.translatesAutoresizingMaskIntoConstraints = false
        indicatorStack.translatesAutoresizingMaskIntoConstraints = false
        titleLabel.translatesAutoresizingMaskIntoConstraints = false
        logoView.translatesAutoresizingMaskIntoConstraints = false
        headerDivider.translatesAutoresizingMaskIntoConstraints = false
        navDivider.translatesAutoresizingMaskIntoConstraints = false

        [headerView, headerDivider, contentContainer, navDivider, bottomBar].forEach { subview in
            view.addSubview(subview)
        }

        headerView.backgroundColor = AppColors.bgHeader
        headerDivider.backgroundColor = AppColors.cardBackground
        navDivider.backgroundColor = AppColors.cardBackground
        bottomBar.backgroundColor = AppColors.bgHeader
        contentContainer.backgroundColor = .clear

        logoView.image = UIImage(named: "Logo")
        logoView.contentMode = .scaleAspectFit
        titleLabel.text = "BayesMech Vision"
        titleLabel.textColor = AppColors.textPrimary
        titleLabel.font = .systemFont(ofSize: 18, weight: .bold)

        headerView.addSubview(logoView)
        headerView.addSubview(titleLabel)

        navButtons.axis = .horizontal
        navButtons.distribution = .fillEqually
        navButtons.alignment = .fill

        indicatorStack.axis = .horizontal
        indicatorStack.distribution = .fillEqually
        indicatorStack.alignment = .fill

        bottomBar.addSubview(indicatorStack)
        bottomBar.addSubview(navButtons)

        configureNavButton(cameraButton, title: "Camera", symbol: "camera.fill", tab: .camera)
        configureNavButton(libraryButton, title: "Library", symbol: "square.stack.3d.up.fill", tab: .library)
        configureNavButton(settingsButton, title: "Settings", symbol: "gearshape.fill", tab: .settings)

        [cameraIndicator, libraryIndicator, settingsIndicator].forEach {
            $0.backgroundColor = AppColors.accentRed
            $0.alpha = 0
            indicatorStack.addArrangedSubview($0)
        }

        [cameraButton, libraryButton, settingsButton].forEach { button in
            navButtons.addArrangedSubview(button)
        }

        let safe = view.safeAreaLayoutGuide
        NSLayoutConstraint.activate([
            headerView.topAnchor.constraint(equalTo: view.topAnchor),
            headerView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            headerView.trailingAnchor.constraint(equalTo: view.trailingAnchor),

            logoView.leadingAnchor.constraint(equalTo: headerView.leadingAnchor, constant: 16),
            logoView.bottomAnchor.constraint(equalTo: headerView.bottomAnchor, constant: -14),
            logoView.widthAnchor.constraint(equalToConstant: 32),
            logoView.heightAnchor.constraint(equalToConstant: 32),
            logoView.topAnchor.constraint(greaterThanOrEqualTo: safe.topAnchor, constant: 8),

            titleLabel.centerYAnchor.constraint(equalTo: logoView.centerYAnchor),
            titleLabel.leadingAnchor.constraint(equalTo: logoView.trailingAnchor, constant: 8),
            titleLabel.trailingAnchor.constraint(lessThanOrEqualTo: headerView.trailingAnchor, constant: -16),

            headerDivider.topAnchor.constraint(equalTo: headerView.bottomAnchor),
            headerDivider.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            headerDivider.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            headerDivider.heightAnchor.constraint(equalToConstant: 1),

            bottomBar.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            bottomBar.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            bottomBar.bottomAnchor.constraint(equalTo: view.bottomAnchor),

            indicatorStack.topAnchor.constraint(equalTo: bottomBar.topAnchor),
            indicatorStack.leadingAnchor.constraint(equalTo: bottomBar.leadingAnchor),
            indicatorStack.trailingAnchor.constraint(equalTo: bottomBar.trailingAnchor),
            indicatorStack.heightAnchor.constraint(equalToConstant: 3),

            navButtons.topAnchor.constraint(equalTo: indicatorStack.bottomAnchor),
            navButtons.leadingAnchor.constraint(equalTo: bottomBar.leadingAnchor),
            navButtons.trailingAnchor.constraint(equalTo: bottomBar.trailingAnchor),
            navButtons.bottomAnchor.constraint(equalTo: safe.bottomAnchor),
            navButtons.heightAnchor.constraint(equalToConstant: 60),

            navDivider.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            navDivider.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            navDivider.bottomAnchor.constraint(equalTo: bottomBar.topAnchor),
            navDivider.heightAnchor.constraint(equalToConstant: 1),

            contentContainer.topAnchor.constraint(equalTo: headerDivider.bottomAnchor),
            contentContainer.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            contentContainer.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            contentContainer.bottomAnchor.constraint(equalTo: navDivider.topAnchor)
        ])
    }

    private func configureNavButton(_ button: UIButton, title: String, symbol: String, tab: Tab) {
        button.tag = tab.rawValue
        button.tintColor = AppColors.navInactive
        button.backgroundColor = .clear
        button.setImage(UIImage(systemName: symbol), for: .normal)
        button.setTitle(nil, for: .normal)
        button.addTarget(self, action: #selector(handleTabTap(_:)), for: .touchUpInside)
    }

    @objc
    private func handleTabTap(_ sender: UIButton) {
        guard let tab = Tab(rawValue: sender.tag) else { return }
        setChromeHidden(false)
        switchToTab(tab)
    }

    private func switchToTab(_ tab: Tab) {
        guard selectedTab != tab || currentChild == nil else { return }

        if selectedTab == .camera {
            cameraViewController.pauseSession()
        }

        selectedTab = tab
        let next: UIViewController
        switch tab {
        case .camera:
            next = cameraViewController
        case .library:
            next = libraryViewController
        case .settings:
            next = settingsViewController
        }

        currentChild?.willMove(toParent: nil)
        currentChild?.view.removeFromSuperview()
        currentChild?.removeFromParent()

        addChild(next)
        contentContainer.addSubview(next.view)
        next.view.pinEdges(to: contentContainer)
        next.didMove(toParent: self)
        currentChild = next

        if tab == .camera {
            cameraViewController.resumeSessionIfNeeded()
        }

        updateNavSelection()
    }

    private func updateNavSelection() {
        let buttons = [cameraButton, libraryButton, settingsButton]
        let indicators = [cameraIndicator, libraryIndicator, settingsIndicator]

        for (index, button) in buttons.enumerated() {
            let isSelected = index == selectedTab.rawValue
            button.tintColor = isSelected ? AppColors.accentRed : AppColors.navInactive
            indicators[index].alpha = isSelected ? 1 : 0
        }
    }

    private func showAnalysis(recording: DataList) {
        let controller = AnalysisViewController(container: container, recording: recording)
        navigationController?.pushViewController(controller, animated: true)
    }

    private func setChromeHidden(_ hidden: Bool) {
        headerView.isHidden = hidden
        headerDivider.isHidden = hidden
        bottomBar.isHidden = hidden
        navDivider.isHidden = hidden
    }
}
