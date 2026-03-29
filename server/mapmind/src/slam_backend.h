#pragma once

#include "config.h"
#include "perceiver.pb.h"

#include <cstdint>
#include <filesystem>
#include <memory>
#include <string>
#include <vector>

namespace mapmind {

struct PoseSample {
  double tx = 0.0;
  double ty = 0.0;
  double tz = 0.0;
  double qx = 0.0;
  double qy = 0.0;
  double qz = 0.0;
  double qw = 1.0;
  bool is_keyframe = false;
};

struct PointSample {
  float x = 0.0f;
  float y = 0.0f;
  float z = 0.0f;
  std::uint8_t r = 0;
  std::uint8_t g = 0;
  std::uint8_t b = 0;
};

struct SlamOutput {
  std::vector<PoseSample> poses;
  std::vector<bayesmech::vision::PerceiverFrameIdentifier> frame_ids;
  std::vector<PointSample> points;
  std::uint32_t loop_closures = 0;
  double processing_time_s = 0.0;
  std::string backend;
  std::string quality;
};

class SlamBackend {
 public:
  virtual ~SlamBackend() = default;

  // Streaming interface: reads frames from disk one at a time to avoid OOM.
  virtual SlamOutput Run(
      const std::filesystem::path & recording_path,
      std::size_t max_frames,
      const SlamConfig & config) = 0;
};

std::unique_ptr<SlamBackend> CreateBackend(const std::string & backend_name);

}  // namespace mapmind
