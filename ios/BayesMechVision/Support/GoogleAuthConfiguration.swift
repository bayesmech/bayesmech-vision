import Foundation

struct GoogleAuthConfiguration {
    let clientID: String
    let serverClientID: String?
    let reversedClientID: String?

    static func load() -> GoogleAuthConfiguration? {
        let bundle = Bundle.main

        let infoClientID = bundle.object(forInfoDictionaryKey: "GIDClientID") as? String
        let infoServerClientID = bundle.object(forInfoDictionaryKey: "GIDServerClientID") as? String
        let urlTypes = bundle.object(forInfoDictionaryKey: "CFBundleURLTypes") as? [[String: Any]]
        let infoReversedClientID = (urlTypes?.first?["CFBundleURLSchemes"] as? [String])?.first

        let plistURL = bundle.url(forResource: "GoogleService-Info", withExtension: "plist")
        let plist = plistURL.flatMap { NSDictionary(contentsOf: $0) as? [String: Any] }

        let clientID = firstNonEmpty(
            plist?["CLIENT_ID"] as? String,
            sanitized(infoClientID)
        )

        let serverClientID = firstNonEmpty(
            plist?["SERVER_CLIENT_ID"] as? String,
            sanitized(infoServerClientID)
        )

        let reversedClientID = firstNonEmpty(
            plist?["REVERSED_CLIENT_ID"] as? String,
            sanitized(infoReversedClientID)
        )

        guard let clientID else { return nil }
        return GoogleAuthConfiguration(
            clientID: clientID,
            serverClientID: serverClientID,
            reversedClientID: reversedClientID
        )
    }

    var isComplete: Bool {
        !clientID.isEmpty && !(reversedClientID?.isEmpty ?? true)
    }

    private static func firstNonEmpty(_ values: String?...) -> String? {
        values.first { value in
            guard let value else { return false }
            return !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        } ?? nil
    }

    private static func sanitized(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        guard !trimmed.contains("YOUR_") else { return nil }
        return trimmed
    }
}
