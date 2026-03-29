#include "config.h"

#include <yaml-cpp/yaml.h>

#include <stdexcept>

namespace mapmind {

AppConfig LoadConfig(const std::filesystem::path & path) {
  const YAML::Node root = YAML::LoadFile(path.string());

  AppConfig config;
  const YAML::Node slam = root["slam"];
  if (!slam) {
    return config;
  }

  if (slam["backend"]) {
    config.slam.backend = slam["backend"].as<std::string>();
  }
  if (slam["quality"]) {
    config.slam.quality = slam["quality"].as<std::string>();
  }
  if (slam["loop_closure"]) {
    config.slam.loop_closure = slam["loop_closure"].as<bool>();
  }
  if (slam["use_imu_gravity"]) {
    config.slam.use_imu_gravity = slam["use_imu_gravity"].as<bool>();
  }
  if (slam["use_gps_priors"]) {
    config.slam.use_gps_priors = slam["use_gps_priors"].as<bool>();
  }
  if (slam["detection_rate_hz"]) {
    config.slam.detection_rate_hz = slam["detection_rate_hz"].as<int>();
  }
  if (slam["linear_update_m"]) {
    config.slam.linear_update_m = slam["linear_update_m"].as<double>();
  }
  if (slam["angular_update_rad"]) {
    config.slam.angular_update_rad = slam["angular_update_rad"].as<double>();
  }

  return config;
}

}  // namespace mapmind
