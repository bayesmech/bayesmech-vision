#include "protoio.h"

#include <arpa/inet.h>

#include <fstream>
#include <stdexcept>
#include <string>

namespace mapmind {

bool ReadDelimitedMessage(std::istream & input, google::protobuf::MessageLite & message) {
  std::uint32_t network_length = 0;
  input.read(reinterpret_cast<char *>(&network_length), sizeof(network_length));
  if (input.gcount() == 0) {
    return false;
  }
  if (input.gcount() != static_cast<std::streamsize>(sizeof(network_length))) {
    throw std::runtime_error("Truncated length prefix while reading protobuf stream.");
  }

  const std::uint32_t length = ntohl(network_length);
  if (length == 0 || length > kFrameSizeLimitBytes) {
    throw std::runtime_error("Invalid protobuf frame length in stream.");
  }

  std::string buffer(length, '\0');
  input.read(buffer.data(), static_cast<std::streamsize>(length));
  if (input.gcount() != static_cast<std::streamsize>(length)) {
    throw std::runtime_error("Truncated protobuf payload while reading stream.");
  }

  if (!message.ParseFromArray(buffer.data(), static_cast<int>(buffer.size()))) {
    throw std::runtime_error("Failed to parse protobuf payload from stream.");
  }

  return true;
}

void WriteDelimitedMessage(std::ostream & output, const google::protobuf::MessageLite & message) {
  const std::string payload = message.SerializeAsString();
  const std::uint32_t network_length = htonl(static_cast<std::uint32_t>(payload.size()));
  output.write(reinterpret_cast<const char *>(&network_length), sizeof(network_length));
  output.write(payload.data(), static_cast<std::streamsize>(payload.size()));
}

std::vector<bayesmech::vision::PerceiverDataFrame> ReadPerceiverFrames(
    const std::filesystem::path & path,
    std::size_t max_frames) {
  std::ifstream input(path, std::ios::binary);
  if (!input) {
    throw std::runtime_error("Unable to open recording: " + path.string());
  }

  std::vector<bayesmech::vision::PerceiverDataFrame> frames;
  while (input.good() && (max_frames == 0 || frames.size() < max_frames)) {
    bayesmech::vision::PerceiverDataFrame frame;
    if (!ReadDelimitedMessage(input, frame)) {
      break;
    }
    frames.push_back(std::move(frame));
  }

  return frames;
}

void AppendMapMindRecords(
    const std::filesystem::path & path,
    const std::vector<bayesmech::vision::MapMindRecord> & records) {
  std::filesystem::create_directories(path.parent_path());
  std::ofstream output(path, std::ios::binary | std::ios::app);
  if (!output) {
    throw std::runtime_error("Unable to open output for append: " + path.string());
  }

  for (const auto & record : records) {
    WriteDelimitedMessage(output, record);
  }
}

}  // namespace mapmind
