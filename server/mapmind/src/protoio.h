#pragma once

#include "mapmind.pb.h"
#include "perceiver.pb.h"

#include <google/protobuf/message_lite.h>

#include <cstdint>
#include <filesystem>
#include <istream>
#include <ostream>
#include <vector>

namespace mapmind {

constexpr std::uint32_t kFrameSizeLimitBytes = 10 * 1024 * 1024;

bool ReadDelimitedMessage(std::istream & input, google::protobuf::MessageLite & message);
void WriteDelimitedMessage(std::ostream & output, const google::protobuf::MessageLite & message);

std::vector<bayesmech::vision::PerceiverDataFrame> ReadPerceiverFrames(
    const std::filesystem::path & path,
    std::size_t max_frames);

void AppendMapMindRecords(
    const std::filesystem::path & path,
    const std::vector<bayesmech::vision::MapMindRecord> & records);

}  // namespace mapmind
