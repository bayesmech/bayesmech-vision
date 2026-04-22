import GoogleSignIn
import UIKit

final class LoginViewController: UIViewController {
    private let container: AppContainer
    private let onSignedIn: (SignedInUser) -> Void

    private let titleLabel = UILabel()
    private let subtitleLabel = UILabel()
    private let descriptionLabel = UILabel()
    private let signInButton = UIButton(type: .system)
    private let statusLabel = UILabel()

    private var attemptedRestore = false
    private var authConfiguration: GoogleAuthConfiguration?

    init(container: AppContainer, onSignedIn: @escaping (SignedInUser) -> Void) {
        self.container = container
        self.onSignedIn = onSignedIn
        super.init(nibName: nil, bundle: nil)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = AppColors.bgDark
        authConfiguration = GoogleAuthConfiguration.load()
        buildUI()
        if authConfiguration == nil {
            statusLabel.text = "Google Sign-In is not configured yet. Add the iOS client ID and reversed URL scheme."
        }
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        guard !attemptedRestore else { return }
        attemptedRestore = true

        guard let authConfiguration else { return }
        GIDSignIn.sharedInstance.configuration = GIDConfiguration(
            clientID: authConfiguration.clientID,
            serverClientID: authConfiguration.serverClientID
        )

        GIDSignIn.sharedInstance.restorePreviousSignIn { [weak self] user, _ in
            guard let self, let user else { return }
            self.finishSignIn(with: user)
        }
    }

    private func buildUI() {
        let scrollView = UIScrollView()
        let contentView = UIView()
        let stackView = UIStackView()

        scrollView.translatesAutoresizingMaskIntoConstraints = false
        contentView.translatesAutoresizingMaskIntoConstraints = false
        stackView.translatesAutoresizingMaskIntoConstraints = false

        stackView.axis = .vertical
        stackView.spacing = 16
        stackView.alignment = .fill

        titleLabel.text = "Welcome to Vision"
        titleLabel.font = .systemFont(ofSize: 32, weight: .bold)
        titleLabel.textColor = AppColors.textPrimary
        titleLabel.numberOfLines = 0

        subtitleLabel.text = "by BayesMech"
        subtitleLabel.font = .systemFont(ofSize: 16, weight: .regular)
        subtitleLabel.textColor = AppColors.textSecondary

        descriptionLabel.text = "We give you accessible intelligence on video and sensor data, build geometric understanding of the world, and tailor it to skill development and experimentation."
        descriptionLabel.font = .systemFont(ofSize: 16, weight: .regular)
        descriptionLabel.textColor = AppColors.textPrimary.withAlphaComponent(0.85)
        descriptionLabel.numberOfLines = 0

        signInButton.setTitle("Continue with Google", for: .normal)
        signInButton.titleLabel?.font = .systemFont(ofSize: 15, weight: .bold)
        signInButton.tintColor = AppColors.textPrimary
        signInButton.backgroundColor = AppColors.cardBackground
        signInButton.layer.cornerRadius = 14
        signInButton.contentEdgeInsets = UIEdgeInsets(top: 16, left: 16, bottom: 16, right: 16)
        signInButton.addTarget(self, action: #selector(handleSignInTap), for: .touchUpInside)

        statusLabel.textColor = AppColors.textSecondary
        statusLabel.font = .systemFont(ofSize: 12, weight: .regular)
        statusLabel.textAlignment = .center
        statusLabel.numberOfLines = 0
        statusLabel.text = "We need you to sign in so that we can store your data securely and run our analyzers on it."

        view.addSubview(scrollView)
        scrollView.addSubview(contentView)
        contentView.addSubview(stackView)

        [titleLabel, subtitleLabel, descriptionLabel, signInButton, statusLabel].forEach { view in
            stackView.addArrangedSubview(view)
        }

        NSLayoutConstraint.activate([
            scrollView.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor),
            scrollView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            scrollView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            scrollView.bottomAnchor.constraint(equalTo: view.bottomAnchor),

            contentView.topAnchor.constraint(equalTo: scrollView.contentLayoutGuide.topAnchor),
            contentView.leadingAnchor.constraint(equalTo: scrollView.contentLayoutGuide.leadingAnchor),
            contentView.trailingAnchor.constraint(equalTo: scrollView.contentLayoutGuide.trailingAnchor),
            contentView.bottomAnchor.constraint(equalTo: scrollView.contentLayoutGuide.bottomAnchor),
            contentView.widthAnchor.constraint(equalTo: scrollView.frameLayoutGuide.widthAnchor),

            stackView.topAnchor.constraint(equalTo: contentView.topAnchor, constant: 96),
            stackView.leadingAnchor.constraint(equalTo: contentView.leadingAnchor, constant: 36),
            stackView.trailingAnchor.constraint(equalTo: contentView.trailingAnchor, constant: -36),
            stackView.bottomAnchor.constraint(equalTo: contentView.bottomAnchor, constant: -64),

            signInButton.heightAnchor.constraint(equalToConstant: 52)
        ])
    }

    @objc
    private func handleSignInTap() {
        guard let authConfiguration else {
            statusLabel.text = "Missing iOS Google Sign-In config. google-services.json is not enough for this app."
            return
        }

        statusLabel.text = "Opening Google Sign-In…"
        GIDSignIn.sharedInstance.configuration = GIDConfiguration(
            clientID: authConfiguration.clientID,
            serverClientID: authConfiguration.serverClientID
        )
        GIDSignIn.sharedInstance.signIn(withPresenting: self) { [weak self] result, error in
            guard let self else { return }
            if let error {
                self.statusLabel.text = "Sign-in failed: \(error.localizedDescription)"
                return
            }
            guard let user = result?.user else {
                self.statusLabel.text = "Sign-in failed. Please try again."
                return
            }
            self.finishSignIn(with: user)
        }
    }

    private func finishSignIn(with user: GIDGoogleUser) {
        let signedInUser = SignedInUser(
            displayName: user.profile?.name ?? user.profile?.email ?? "unknown",
            email: user.profile?.email ?? "",
            authToken: user.userID ?? ""
        )
        statusLabel.text = nil
        container.stateStore.setCurrentUser(signedInUser)
        onSignedIn(signedInUser)
    }
}
