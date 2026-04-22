import Combine
import UIKit

final class LibraryViewController: UIViewController, UITableViewDataSource, UITableViewDelegate, UITextFieldDelegate {
    var onSelectRecording: ((DataList) -> Void)?

    private let container: AppContainer
    private let searchField = UITextField()
    private let tableView = UITableView(frame: .zero, style: .plain)
    private let refreshControl = UIRefreshControl()
    private var cancellables: Set<AnyCancellable> = []
    private var filteredRecordings: [DataList] = []

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
        refreshRecordings()
    }

    private func buildUI() {
        searchField.translatesAutoresizingMaskIntoConstraints = false
        tableView.translatesAutoresizingMaskIntoConstraints = false

        searchField.backgroundColor = AppColors.cardBackground
        searchField.textColor = AppColors.textPrimary
        searchField.attributedPlaceholder = NSAttributedString(
            string: "Select experiences",
            attributes: [.foregroundColor: AppColors.textSecondary]
        )
        searchField.font = .systemFont(ofSize: 15, weight: .regular)
        searchField.borderStyle = .none
        searchField.leftView = UIImageView(image: UIImage(systemName: "magnifyingglass"))
        searchField.leftView?.tintColor = AppColors.textSecondary
        searchField.leftViewMode = .always
        searchField.layer.cornerRadius = 16
        searchField.layer.masksToBounds = true
        searchField.addTarget(self, action: #selector(searchTextDidChange), for: .editingChanged)

        tableView.backgroundColor = .clear
        tableView.separatorColor = AppColors.divider
        tableView.dataSource = self
        tableView.delegate = self
        tableView.register(UITableViewCell.self, forCellReuseIdentifier: "RecordingCell")
        tableView.refreshControl = refreshControl
        refreshControl.addTarget(self, action: #selector(handlePullToRefresh), for: .valueChanged)

        view.addSubview(searchField)
        view.addSubview(tableView)

        NSLayoutConstraint.activate([
            searchField.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 28),
            searchField.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 16),
            searchField.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -16),
            searchField.heightAnchor.constraint(equalToConstant: 48),

            tableView.topAnchor.constraint(equalTo: searchField.bottomAnchor, constant: 12),
            tableView.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 16),
            tableView.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -16),
            tableView.bottomAnchor.constraint(equalTo: view.bottomAnchor)
        ])
    }

    private func bindState() {
        container.stateStore.$recordings
            .receive(on: DispatchQueue.main)
            .sink { [weak self] recordings in
                self?.applyFilter(recordings: recordings)
            }
            .store(in: &cancellables)
    }

    @objc
    private func searchTextDidChange() {
        applyFilter(recordings: container.stateStore.recordings)
    }

    @objc
    private func handlePullToRefresh() {
        refreshRecordings()
    }

    private func refreshRecordings() {
        refreshControl.beginRefreshing()
        Task {
            defer {
                DispatchQueue.main.async {
                    self.refreshControl.endRefreshing()
                }
            }

            do {
                let recordings = try await container.insightRepository.listRecordings(
                    serverURL: container.stateStore.serverURL,
                    user: container.stateStore.currentUser
                )
                DispatchQueue.main.async {
                    self.container.stateStore.updateRecordings(recordings)
                }
            } catch {
                print("Failed to refresh recordings: \(error)")
            }
        }
    }

    private func applyFilter(recordings: [DataList]) {
        let query = searchField.text?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() ?? ""
        if query.isEmpty {
            filteredRecordings = recordings
        } else {
            filteredRecordings = recordings.filter { item in
                item.title.lowercased().contains(query)
                    || item.fileName.lowercased().contains(query)
                    || item.previewText.lowercased().contains(query)
                    || item.tags.contains(where: { $0.lowercased().contains(query) })
            }
        }
        tableView.reloadData()
    }

    func tableView(_ tableView: UITableView, numberOfRowsInSection section: Int) -> Int {
        filteredRecordings.count
    }

    func tableView(_ tableView: UITableView, cellForRowAt indexPath: IndexPath) -> UITableViewCell {
        let cell = tableView.dequeueReusableCell(withIdentifier: "RecordingCell", for: indexPath)
        let item = filteredRecordings[indexPath.row]
        var content = cell.defaultContentConfiguration()
        content.text = item.title.isEmpty ? item.fileName : item.title
        content.secondaryText = item.previewText.isEmpty ? item.fileName : item.previewText
        content.textProperties.color = AppColors.textPrimary
        content.secondaryTextProperties.color = AppColors.textSecondary
        content.image = UIImage(systemName: "film")
        if !item.imageFrame.isEmpty, let previewImage = UIImage(data: item.imageFrame) {
            content.image = previewImage
        }
        cell.contentConfiguration = content
        cell.backgroundColor = AppColors.cardBackground
        cell.layer.cornerRadius = 16
        cell.layer.masksToBounds = true
        cell.accessoryType = .disclosureIndicator
        return cell
    }

    func tableView(_ tableView: UITableView, didSelectRowAt indexPath: IndexPath) {
        tableView.deselectRow(at: indexPath, animated: true)
        onSelectRecording?(filteredRecordings[indexPath.row])
    }
}
