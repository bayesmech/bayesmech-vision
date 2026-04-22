import Foundation

struct TranscriptionClient {
    private let session: URLSession = .shared

    func transcribe(audioFileURL: URL, serverURL: String) async throws -> String {
        let base = HTTPBase.from(serverURL: serverURL)
        guard let url = URL(string: "\(base)/api/transcribe") else {
            throw URLError(.badURL)
        }

        let boundary = "Boundary-\(UUID().uuidString)"
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")

        let data = try Data(contentsOf: audioFileURL)
        var body = Data()
        body.appendString("--\(boundary)\r\n")
        body.appendString("Content-Disposition: form-data; name=\"file\"; filename=\"\(audioFileURL.lastPathComponent)\"\r\n")
        body.appendString("Content-Type: audio/mp4\r\n\r\n")
        body.append(data)
        body.appendString("\r\n--\(boundary)--\r\n")

        let (responseData, response) = try await session.upload(for: request, from: body)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw URLError(.badServerResponse)
        }
        guard (200..<300).contains(httpResponse.statusCode) else {
            throw NSError(domain: "TranscriptionClient", code: httpResponse.statusCode, userInfo: [
                NSLocalizedDescriptionKey: "Streamlog transcription failed with \(httpResponse.statusCode)"
            ])
        }

        let payload = try JSONSerialization.jsonObject(with: responseData, options: []) as? [String: Any]
        let text = (payload?["text"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        guard !text.isEmpty else {
            throw NSError(domain: "TranscriptionClient", code: -1, userInfo: [
                NSLocalizedDescriptionKey: "Streamlog transcription returned an empty transcript"
            ])
        }
        return text
    }
}

private extension Data {
    mutating func appendString(_ value: String) {
        append(value.data(using: .utf8)!)
    }
}
