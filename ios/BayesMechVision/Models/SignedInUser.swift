import Foundation

struct SignedInUser: Codable, Sendable {
    var displayName: String
    var email: String
    var authToken: String
}
