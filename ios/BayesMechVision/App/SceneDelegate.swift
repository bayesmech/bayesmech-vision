import GoogleSignIn
import UIKit

final class SceneDelegate: UIResponder, UIWindowSceneDelegate {
    var window: UIWindow?
    private let container = AppContainer()

    func scene(
        _ scene: UIScene,
        willConnectTo session: UISceneSession,
        options connectionOptions: UIScene.ConnectionOptions
    ) {
        guard let windowScene = scene as? UIWindowScene else { return }

        let navigationController = UINavigationController()
        navigationController.setNavigationBarHidden(true, animated: false)
        navigationController.view.backgroundColor = AppColors.bgDark

        let loginViewController = LoginViewController(container: container) { [weak self, weak navigationController] user in
            self?.container.stateStore.setCurrentUser(user)
            let root = RootShellViewController(container: self?.container ?? AppContainer())
            navigationController?.setViewControllers([root], animated: true)
        }
        let initialController: UIViewController
        if container.stateStore.currentUser != nil {
            initialController = RootShellViewController(container: container)
        } else {
            initialController = loginViewController
        }

        navigationController.setViewControllers([initialController], animated: false)

        let window = UIWindow(windowScene: windowScene)
        window.rootViewController = navigationController
        window.tintColor = AppColors.accentRed
        self.window = window
        window.makeKeyAndVisible()
    }

    func sceneWillResignActive(_ scene: UIScene) {
        container.arCoordinator.stop()
    }

    func scene(_ scene: UIScene, openURLContexts URLContexts: Set<UIOpenURLContext>) {
        guard let url = URLContexts.first?.url else { return }
        GIDSignIn.sharedInstance.handle(url)
    }
}
