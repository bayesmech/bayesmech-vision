#pragma once

#include <filesystem>
#include <string>

namespace mapmind {

struct SlamConfig {
  std::string backend = "rtabmap";
  std::string quality = "fast";
  bool loop_closure = true;
  bool use_imu_gravity = true;
  bool use_gps_priors = true;
  int detection_rate_hz = 1;
  double linear_update_m = 0.0;
  double angular_update_rad = 0.0;
};

struct AppConfig {
  SlamConfig slam;
};

AppConfig LoadConfig(const std::filesystem::path & path);

}  // namespace mapmind
