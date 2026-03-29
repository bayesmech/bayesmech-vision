#include "protoio.h"
#include "slam_backend.h"

#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <set>
#include <stdexcept>
#include <string>
#include <tuple>
#include <unordered_map>
#include <utility>
#include <vector>

#if MAPMIND_HAVE_RTABMAP
#include <Eigen/Geometry>
#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>
#include <pcl/point_cloud.h>
#include <pcl/point_types.h>
#include <rtabmap/core/CameraModel.h>
#include <rtabmap/core/GPS.h>
#include <rtabmap/core/IMU.h>
#include <rtabmap/core/Link.h>
#include <rtabmap/core/Memory.h>
#include <rtabmap/core/Parameters.h>
#include <rtabmap/core/Rtabmap.h>
#include <rtabmap/core/SensorData.h>
#include <rtabmap/core/Transform.h>
#include <rtabmap/core/util3d.h>
#include <rtabmap/core/util3d_filtering.h>
#include <rtabmap/core/util3d_transforms.h>
#endif

namespace mapmind {

namespace {

#if MAPMIND_HAVE_RTABMAP

using Frame = bayesmech::vision::PerceiverDataFrame;

cv::Mat DecodeRgbFrame(const Frame & frame) {
  const auto & rgb = frame.rgb_frame();
  switch (rgb.format()) {
    case bayesmech::vision::ImageFrame::JPEG: {
      if (rgb.data().empty()) {
        return cv::Mat();
      }
      const auto * bytes = reinterpret_cast<const std::uint8_t *>(rgb.data().data());
      const cv::Mat encoded(1, static_cast<int>(rgb.data().size()), CV_8UC1,
                            const_cast<std::uint8_t *>(bytes));
      return cv::imdecode(encoded, cv::IMREAD_COLOR);
    }
    case bayesmech::vision::ImageFrame::BITMAP_RGB: {
      if (!frame.has_camera_intrinsics()) {
        throw std::runtime_error("RGB bitmap frame is missing camera intrinsics.");
      }
      const auto & intr = frame.camera_intrinsics();
      const int width = intr.image_width() > 0.0f ? static_cast<int>(intr.image_width()) : 0;
      const int height = intr.image_height() > 0.0f ? static_cast<int>(intr.image_height()) : 0;
      if (width <= 0 || height <= 0) {
        throw std::runtime_error("RGB bitmap frame has invalid dimensions.");
      }
      const std::size_t expected = static_cast<std::size_t>(width) * static_cast<std::size_t>(height) * 3u;
      if (rgb.data().size() != expected) {
        throw std::runtime_error("RGB bitmap payload size does not match intrinsics.");
      }
      cv::Mat rgb_mat(height, width, CV_8UC3, const_cast<char *>(rgb.data().data()));
      cv::Mat bgr_mat;
      cv::cvtColor(rgb_mat, bgr_mat, cv::COLOR_RGB2BGR);
      return bgr_mat.clone();
    }
    case bayesmech::vision::ImageFrame::BITMAP_RGBA: {
      if (!frame.has_camera_intrinsics()) {
        throw std::runtime_error("RGBA bitmap frame is missing camera intrinsics.");
      }
      const auto & intr = frame.camera_intrinsics();
      const int width = intr.image_width() > 0.0f ? static_cast<int>(intr.image_width()) : 0;
      const int height = intr.image_height() > 0.0f ? static_cast<int>(intr.image_height()) : 0;
      if (width <= 0 || height <= 0) {
        throw std::runtime_error("RGBA bitmap frame has invalid dimensions.");
      }
      const std::size_t expected = static_cast<std::size_t>(width) * static_cast<std::size_t>(height) * 4u;
      if (rgb.data().size() != expected) {
        throw std::runtime_error("RGBA bitmap payload size does not match intrinsics.");
      }
      cv::Mat rgba_mat(height, width, CV_8UC4, const_cast<char *>(rgb.data().data()));
      cv::Mat bgr_mat;
      cv::cvtColor(rgba_mat, bgr_mat, cv::COLOR_RGBA2BGR);
      return bgr_mat.clone();
    }
    case bayesmech::vision::ImageFrame::GRAYSCALE: {
      if (!frame.has_camera_intrinsics()) {
        throw std::runtime_error("Grayscale frame is missing camera intrinsics.");
      }
      const auto & intr = frame.camera_intrinsics();
      const int width = intr.image_width() > 0.0f ? static_cast<int>(intr.image_width()) : 0;
      const int height = intr.image_height() > 0.0f ? static_cast<int>(intr.image_height()) : 0;
      if (width <= 0 || height <= 0) {
        throw std::runtime_error("Grayscale frame has invalid dimensions.");
      }
      const std::size_t expected = static_cast<std::size_t>(width) * static_cast<std::size_t>(height);
      if (rgb.data().size() != expected) {
        throw std::runtime_error("Grayscale payload size does not match intrinsics.");
      }
      cv::Mat gray_mat(height, width, CV_8UC1, const_cast<char *>(rgb.data().data()));
      cv::Mat bgr_mat;
      cv::cvtColor(gray_mat, bgr_mat, cv::COLOR_GRAY2BGR);
      return bgr_mat.clone();
    }
    default:
      throw std::runtime_error("Unsupported RGB frame format for RTAB-Map backend.");
  }
}

cv::Mat DecodeDepthFrame(const Frame & frame, const cv::Size & rgb_size) {
  if (!frame.has_depth_frame() || frame.depth_frame().data().empty()) {
    return cv::Mat();
  }

  const auto & depth_frame = frame.depth_frame();
  int width = rgb_size.width;
  int height = rgb_size.height;
  if (frame.has_camera_intrinsics()) {
    const auto & intr = frame.camera_intrinsics();
    if (intr.depth_width() > 0.0f && intr.depth_height() > 0.0f) {
      width = static_cast<int>(intr.depth_width());
      height = static_cast<int>(intr.depth_height());
    }
  }
  if (width <= 0 || height <= 0) {
    throw std::runtime_error("Depth frame dimensions are invalid.");
  }

  cv::Mat depth;
  switch (depth_frame.format()) {
    case bayesmech::vision::DepthFrame::UINT16_MILLIMETERS: {
      const std::size_t expected =
          static_cast<std::size_t>(width) * static_cast<std::size_t>(height) * sizeof(std::uint16_t);
      if (depth_frame.data().size() != expected) {
        throw std::runtime_error("Depth payload size does not match UINT16 dimensions.");
      }
      cv::Mat depth_mm(height, width, CV_16UC1, const_cast<char *>(depth_frame.data().data()));
      depth_mm.convertTo(depth, CV_32FC1, 1.0 / 1000.0);
      break;
    }
    case bayesmech::vision::DepthFrame::FLOAT32_METERS: {
      const std::size_t expected =
          static_cast<std::size_t>(width) * static_cast<std::size_t>(height) * sizeof(float);
      if (depth_frame.data().size() != expected) {
        throw std::runtime_error("Depth payload size does not match FLOAT32 dimensions.");
      }
      cv::Mat depth_m(height, width, CV_32FC1, const_cast<char *>(depth_frame.data().data()));
      depth = depth_m.clone();
      break;
    }
    default:
      throw std::runtime_error("Unsupported depth frame format for RTAB-Map backend.");
  }

  if (depth.size() != rgb_size) {
    cv::Mat resized;
    cv::resize(depth, resized, rgb_size, 0.0, 0.0, cv::INTER_NEAREST);
    depth = resized;
  }
  return depth;
}

rtabmap::Transform FramePoseWorldToOpenGL(const Frame & frame) {
  if (!frame.has_camera_pose()) {
    return rtabmap::Transform();
  }
  const auto & pose = frame.camera_pose();
  return rtabmap::Transform(
      pose.position().x(),
      pose.position().y(),
      pose.position().z(),
      pose.rotation().x(),
      pose.rotation().y(),
      pose.rotation().z(),
      pose.rotation().w());
}

rtabmap::Transform FramePoseWorldToRtabmap(const Frame & frame) {
  const rtabmap::Transform world_t_opengl = FramePoseWorldToOpenGL(frame);
  if (world_t_opengl.isNull()) {
    return world_t_opengl;
  }
  return world_t_opengl * rtabmap::Transform::opengl_T_rtabmap();
}

rtabmap::CameraModel BuildCameraModel(const Frame & frame, const cv::Size & image_size) {
  if (!frame.has_camera_intrinsics()) {
    throw std::runtime_error("Recording frame is missing camera intrinsics.");
  }
  const auto & intr = frame.camera_intrinsics();
  if (intr.fx() == 0.0f || intr.fy() == 0.0f) {
    throw std::runtime_error("Recording frame has invalid camera intrinsics.");
  }
  return rtabmap::CameraModel(
      intr.fx(),
      intr.fy(),
      intr.cx(),
      intr.cy(),
      rtabmap::Transform::getIdentity(),
      0.0,
      image_size);
}

// Compute orientation quaternion from gravity and magnetometer vectors (both in device frame).
// Gravity defines pitch/roll; magnetometer adds yaw. Returns (qx, qy, qz, qw).
cv::Vec4d OrientationFromGravityAndMag(const cv::Vec3d & gravity, const cv::Vec3d & mag) {
  cv::Vec3d down = cv::normalize(gravity);

  // East = down x magnetic_north (perpendicular to both gravity and mag field)
  cv::Vec3d east = down.cross(cv::normalize(mag));
  const double east_norm = cv::norm(east);
  if (east_norm < 1e-6) {
    return cv::Vec4d(0.0, 0.0, 0.0, 1.0);
  }
  east /= east_norm;

  // North = east x down (horizontal component pointing toward magnetic north)
  cv::Vec3d north = east.cross(down);

  // Rotation matrix R: world-NED-from-device.
  // Rows are the world NED axes expressed in device coordinates.
  Eigen::Matrix3d R;
  R.row(0) = Eigen::Vector3d(north[0], north[1], north[2]);
  R.row(1) = Eigen::Vector3d(east[0],  east[1],  east[2]);
  R.row(2) = Eigen::Vector3d(down[0],  down[1],  down[2]);

  Eigen::Quaterniond q(R);
  q.normalize();
  return cv::Vec4d(q.x(), q.y(), q.z(), q.w());
}

// Compute orientation from gravity alone (no magnetometer) — yaw is unobservable so set to 0.
cv::Vec4d OrientationFromGravity(const cv::Vec3d & gravity) {
  cv::Vec3d down = cv::normalize(gravity);

  // Pick an arbitrary horizontal axis perpendicular to gravity
  cv::Vec3d arbitrary = (std::abs(down[0]) < 0.9) ? cv::Vec3d(1, 0, 0) : cv::Vec3d(0, 1, 0);
  cv::Vec3d east = down.cross(arbitrary);
  east /= cv::norm(east);
  cv::Vec3d north = east.cross(down);

  Eigen::Matrix3d R;
  R.row(0) = Eigen::Vector3d(north[0], north[1], north[2]);
  R.row(1) = Eigen::Vector3d(east[0],  east[1],  east[2]);
  R.row(2) = Eigen::Vector3d(down[0],  down[1],  down[2]);

  Eigen::Quaterniond q(R);
  q.normalize();
  return cv::Vec4d(q.x(), q.y(), q.z(), q.w());
}

// Build an rtabmap::IMU from a PerceiverDataFrame, if IMU data is present.
// localTransform converts from Android sensor frame (≈ OpenGL) to RTAB-Map camera frame.
rtabmap::IMU BuildIMU(const Frame & frame) {
  if (!frame.has_imu_data()) {
    return rtabmap::IMU();
  }
  const auto & imu = frame.imu_data();

  // Angular velocity (rad/s) — gyroscope
  const auto & av = imu.angular_velocity();
  cv::Vec3d angular_velocity(av.x(), av.y(), av.z());
  cv::Mat angular_velocity_cov = cv::Mat::eye(3, 3, CV_64F) * 0.01;

  // Raw accelerometer = linear_acceleration + gravity (ARCore separates them)
  const auto & la = imu.linear_acceleration();
  const auto & gv = imu.gravity();
  cv::Vec3d linear_acceleration(
      la.x() + gv.x(),
      la.y() + gv.y(),
      la.z() + gv.z());
  cv::Mat linear_acceleration_cov = cv::Mat::eye(3, 3, CV_64F) * 0.1;

  // Orientation from gravity (+ magnetometer if available)
  const auto & mf = imu.magnetic_field();
  const double gravity_norm = cv::norm(cv::Vec3d(gv.x(), gv.y(), gv.z()));
  const double mag_norm = cv::norm(cv::Vec3d(mf.x(), mf.y(), mf.z()));

  cv::Vec4d orientation(0.0, 0.0, 0.0, 1.0);
  cv::Mat orientation_cov;

  if (gravity_norm > 1e-3 && mag_norm > 1e-3) {
    orientation = OrientationFromGravityAndMag(
        cv::Vec3d(gv.x(), gv.y(), gv.z()),
        cv::Vec3d(mf.x(), mf.y(), mf.z()));
    // Lower covariance when we have both gravity and magnetometer
    orientation_cov = cv::Mat::eye(3, 3, CV_64F) * 0.01;
  } else if (gravity_norm > 1e-3) {
    orientation = OrientationFromGravity(cv::Vec3d(gv.x(), gv.y(), gv.z()));
    // Higher yaw uncertainty without magnetometer
    orientation_cov = cv::Mat::eye(3, 3, CV_64F) * 0.1;
    orientation_cov.at<double>(2, 2) = 99999.0;  // yaw unknown
  }

  // Local transform: Android sensor frame (≈ OpenGL convention) → RTAB-Map camera frame
  const rtabmap::Transform local_transform = rtabmap::Transform::rtabmap_T_opengl();

  return rtabmap::IMU(
      orientation, orientation_cov,
      angular_velocity, angular_velocity_cov,
      linear_acceleration, linear_acceleration_cov,
      local_transform);
}

// Build an rtabmap::GPS from a PerceiverDataFrame, if GPS data is present.
rtabmap::GPS BuildGPS(const Frame & frame) {
  if (!frame.has_gps_location()) {
    return rtabmap::GPS();
  }
  const auto & gps = frame.gps_location();
  if (gps.latitude() == 0.0 && gps.longitude() == 0.0) {
    return rtabmap::GPS();  // no fix
  }
  const double stamp = gps.timestamp_ms() > 0
      ? static_cast<double>(gps.timestamp_ms()) / 1000.0
      : (frame.frame_identifier().timestamp_ns() > 0
          ? static_cast<double>(frame.frame_identifier().timestamp_ns()) / 1e9
          : 0.0);
  return rtabmap::GPS(
      stamp,
      gps.longitude(),
      gps.latitude(),
      gps.altitude(),
      static_cast<double>(gps.accuracy()),
      static_cast<double>(gps.bearing()));
}

PoseSample PoseSampleFromOpenGLTransform(const rtabmap::Transform & world_t_opengl, bool is_keyframe) {
  PoseSample sample;
  if (!world_t_opengl.isNull()) {
    const Eigen::Quaternionf q = world_t_opengl.getQuaternionf();
    sample.tx = world_t_opengl.x();
    sample.ty = world_t_opengl.y();
    sample.tz = world_t_opengl.z();
    sample.qx = q.x();
    sample.qy = q.y();
    sample.qz = q.z();
    sample.qw = q.w();
  }
  sample.is_keyframe = is_keyframe;
  return sample;
}

std::uint32_t CountLoopClosures(const std::multimap<int, rtabmap::Link> & constraints) {
  std::set<std::tuple<int, int, int>> unique_closures;
  for (const auto & entry : constraints) {
    const auto & link = entry.second;
    const auto type = link.type();
    if (type != rtabmap::Link::kGlobalClosure &&
        type != rtabmap::Link::kLocalSpaceClosure &&
        type != rtabmap::Link::kLocalTimeClosure &&
        type != rtabmap::Link::kUserClosure) {
      continue;
    }
    const int from = std::min(link.from(), link.to());
    const int to = std::max(link.from(), link.to());
    unique_closures.emplace(from, to, static_cast<int>(type));
  }
  return static_cast<std::uint32_t>(unique_closures.size());
}

std::vector<PointSample> ConvertCloudToSamples(
    const pcl::PointCloud<pcl::PointXYZRGB>::Ptr & cloud) {
  std::vector<PointSample> points;
  if (!cloud) {
    return points;
  }
  points.reserve(cloud->size());
  for (const auto & point : cloud->points) {
    if (!std::isfinite(point.x) || !std::isfinite(point.y) || !std::isfinite(point.z)) {
      continue;
    }
    PointSample sample;
    sample.x = point.x;
    sample.y = point.y;
    sample.z = point.z;
    sample.r = point.r;
    sample.g = point.g;
    sample.b = point.b;
    points.push_back(sample);
  }
  return points;
}

pcl::PointCloud<pcl::PointXYZRGB>::Ptr BuildCloudFromSensorDataMap(
    const std::map<int, rtabmap::Transform> & optimized_poses,
    const std::unordered_map<int, rtabmap::SensorData> & sensor_data_map,
    float voxel_size,
    int decimation) {
  auto merged = pcl::PointCloud<pcl::PointXYZRGB>::Ptr(new pcl::PointCloud<pcl::PointXYZRGB>());
  for (const auto & entry : optimized_poses) {
    const int id = entry.first;
    const auto it = sensor_data_map.find(id);
    if (it == sensor_data_map.end()) {
      continue;
    }
    auto cloud = rtabmap::util3d::cloudRGBFromSensorData(
        it->second, decimation, 8.0f, 0.2f);
    if (!cloud || cloud->empty()) {
      continue;
    }
    cloud = rtabmap::util3d::transformPointCloud(cloud, entry.second);
    *merged += *cloud;
  }
  if (voxel_size > 0.0f && !merged->empty()) {
    merged = rtabmap::util3d::voxelize(merged, voxel_size);
  }
  return merged;
}

std::vector<PointSample> BuildDensePointCloudFromFrames(
    const std::vector<Frame> & frames,
    const std::vector<PoseSample> & poses,
    int frame_subsample,
    int pixel_stride,
    float voxel_size,
    float min_depth,
    float max_depth) {
  std::map<std::tuple<int, int, int>, PointSample> voxels;

  for (std::size_t i = 0; i < frames.size(); i += static_cast<std::size_t>(frame_subsample)) {
    const auto & frame = frames[i];
    if (!frame.has_camera_intrinsics() || i >= poses.size()) {
      continue;
    }

    cv::Mat rgb = DecodeRgbFrame(frame);
    if (rgb.empty()) {
      continue;
    }
    cv::Mat depth = DecodeDepthFrame(frame, rgb.size());
    if (depth.empty()) {
      continue;
    }

    const auto & intr = frame.camera_intrinsics();
    if (intr.fx() == 0.0f || intr.fy() == 0.0f) {
      continue;
    }

    const PoseSample & pose = poses[i];
    Eigen::Quaterniond q(pose.qw, pose.qx, pose.qy, pose.qz);
    if (!std::isfinite(q.norm()) || q.norm() == 0.0) {
      continue;
    }
    q.normalize();
    const Eigen::Matrix3d rotation = q.toRotationMatrix();
    const Eigen::Vector3d translation(pose.tx, pose.ty, pose.tz);

    for (int v = 0; v < depth.rows; v += pixel_stride) {
      for (int u = 0; u < depth.cols; u += pixel_stride) {
        const float d = depth.at<float>(v, u);
        if (!std::isfinite(d) || d < min_depth || d > max_depth) {
          continue;
        }

        Eigen::Vector3d point_camera(
            (static_cast<double>(u) - intr.cx()) * d / intr.fx(),
            (static_cast<double>(v) - intr.cy()) * d / intr.fy(),
            -static_cast<double>(d));
        Eigen::Vector3d point_world = rotation * point_camera + translation;

        const cv::Vec3b bgr = rgb.at<cv::Vec3b>(v, u);
        PointSample sample;
        sample.x = static_cast<float>(point_world.x());
        sample.y = static_cast<float>(point_world.y());
        sample.z = static_cast<float>(point_world.z());
        sample.r = bgr[2];
        sample.g = bgr[1];
        sample.b = bgr[0];

        const auto key = std::make_tuple(
            static_cast<int>(std::llround(sample.x / voxel_size)),
            static_cast<int>(std::llround(sample.y / voxel_size)),
            static_cast<int>(std::llround(sample.z / voxel_size)));
        voxels.emplace(key, sample);
      }
    }
  }

  std::vector<PointSample> points;
  points.reserve(voxels.size());
  for (const auto & entry : voxels) {
    points.push_back(entry.second);
  }
  return points;
}

pcl::PointCloud<pcl::PointXYZRGB>::Ptr BuildMergedCloud(
    const std::map<int, rtabmap::Transform> & optimized_poses,
    const std::map<int, rtabmap::Signature> & signatures,
    int decimation,
    float voxel_size) {
  auto merged = pcl::PointCloud<pcl::PointXYZRGB>::Ptr(new pcl::PointCloud<pcl::PointXYZRGB>());
  for (const auto & pose_entry : optimized_poses) {
    const auto sig_iter = signatures.find(pose_entry.first);
    if (sig_iter == signatures.end()) {
      continue;
    }
    const auto & sensor_data = sig_iter->second.sensorData();
    if (!sensor_data.isValid()) {
      continue;
    }
    auto cloud = rtabmap::util3d::cloudRGBFromSensorData(sensor_data, decimation, 8.0f, 0.25f);
    if (!cloud || cloud->empty()) {
      continue;
    }
    cloud = rtabmap::util3d::transformPointCloud(cloud, pose_entry.second);
    *merged += *cloud;
  }
  if (voxel_size > 0.0f && !merged->empty()) {
    merged = rtabmap::util3d::voxelize(merged, voxel_size);
  }
  return merged;
}

#endif  // MAPMIND_HAVE_RTABMAP

class RtabmapBackend final : public SlamBackend {
 public:
  SlamOutput Run(
      const std::filesystem::path & recording_path,
      std::size_t max_frames,
      const SlamConfig & config) override {
#if MAPMIND_HAVE_RTABMAP
    const auto start_time = std::chrono::steady_clock::now();
    const auto log_elapsed = [&]() -> std::string {
      const double secs = std::chrono::duration<double>(
          std::chrono::steady_clock::now() - start_time).count();
      char buf[32];
      std::snprintf(buf, sizeof(buf), "%.1fs", secs);
      return std::string(buf);
    };

    SlamOutput output;
    output.backend = "rtabmap";
    output.quality = config.quality;

    // ── Pass 1: stream frames from disk, run SLAM one at a time ─────────────
    std::cout << "[rtabmap] loading " << recording_path.filename().string() << '\n';

    rtabmap::ParametersMap parameters;
    parameters.insert(std::make_pair(
        rtabmap::Parameters::kRtabmapDetectionRate(),
        std::to_string(config.detection_rate_hz)));
    parameters.insert(std::make_pair(
        rtabmap::Parameters::kRGBDLinearUpdate(),
        std::to_string(config.linear_update_m)));
    parameters.insert(std::make_pair(
        rtabmap::Parameters::kRGBDAngularUpdate(),
        std::to_string(config.angular_update_rad)));
    parameters.insert(std::make_pair(
        rtabmap::Parameters::kMemIncrementalMemory(),
        std::string("true")));
    parameters.insert(std::make_pair(
        rtabmap::Parameters::kRGBDLoopClosureReextractFeatures(),
        std::string("false")));

    if (!config.loop_closure) {
      parameters.insert(std::make_pair(
          rtabmap::Parameters::kMemRehearsalSimilarity(),
          std::string("1.1")));
    }

    if (config.quality == "fast") {
      parameters.insert(std::make_pair(
          rtabmap::Parameters::kVisMaxFeatures(),
          std::string("600")));
      parameters.insert(std::make_pair(
          rtabmap::Parameters::kVisMinInliers(),
          std::string("12")));
    } else {
      parameters.insert(std::make_pair(
          rtabmap::Parameters::kVisMaxFeatures(),
          std::string("1200")));
      parameters.insert(std::make_pair(
          rtabmap::Parameters::kVisMinInliers(),
          std::string("15")));
    }

    if (config.use_imu_gravity) {
      parameters.insert(std::make_pair(
          rtabmap::Parameters::kOptimizerGravitySigma(),
          std::string("0.3")));
    }

    if (config.use_gps_priors) {
      // Use g2o optimizer (supports GPS priors; TORO does not).
      parameters.insert(std::make_pair(
          rtabmap::Parameters::kOptimizerStrategy(),
          std::string("1")));
      // Critical: default is true, which silently drops all GPS constraints.
      parameters.insert(std::make_pair(
          rtabmap::Parameters::kOptimizerPriorsIgnored(),
          std::string("false")));
    }

    rtabmap::Rtabmap rtabmap;
    rtabmap.init(parameters);

    // ── Pass 1: stream frames, run SLAM ─────────────────────────────────────
    // No images are retained — only a node-ID → frame-index map for pass 2.
    auto open_recording = [&]() -> std::ifstream {
      std::ifstream f(recording_path, std::ios::binary);
      if (!f) {
        throw std::runtime_error("Unable to open recording: " + recording_path.string());
      }
      return f;
    };

    std::streamoff file_size = 0;
    {
      std::ifstream probe(recording_path, std::ios::binary | std::ios::ate);
      if (probe) file_size = probe.tellg();
    }

    std::cout << "[rtabmap] pass 1/2: SLAM processing...\n";
    std::cout.flush();

    int processed_frames = 0;
    std::size_t frame_index = 0;
    // Maps RTAB-Map node ID → frame index (for pass 2 selective re-read).
    std::unordered_map<int, std::size_t> node_to_frame;
    auto last_progress_time = std::chrono::steady_clock::now();

    {
      auto input = open_recording();
      while (input.good() && (max_frames == 0 || frame_index < max_frames)) {
        bayesmech::vision::PerceiverDataFrame frame;
        if (!ReadDelimitedMessage(input, frame)) {
          break;
        }
        const std::size_t i = frame_index++;

        // Progress: log every 2 seconds.
        const auto now = std::chrono::steady_clock::now();
        if (std::chrono::duration<double>(now - last_progress_time).count() >= 2.0 || i == 0) {
          const double pct = file_size > 0
              ? 100.0 * static_cast<double>(input.tellg()) / static_cast<double>(file_size)
              : 0.0;
          std::cout << "[rtabmap] [" << log_elapsed() << "] frame " << (i + 1)
                    << " (" << static_cast<int>(pct) << "% of file)"
                    << " | " << processed_frames << " processed\n";
          std::cout.flush();
          last_progress_time = now;
        }

        // Store frame identifier for output.
        if (frame.has_frame_identifier()) {
          output.frame_ids.push_back(frame.frame_identifier());
        } else {
          bayesmech::vision::PerceiverFrameIdentifier fid;
          fid.set_frame_number(static_cast<std::uint32_t>(i));
          output.frame_ids.push_back(std::move(fid));
        }

        // Store ARCore pose as fallback (overwritten later if SLAM optimises it).
        const rtabmap::Transform world_t_opengl = FramePoseWorldToOpenGL(frame);
        output.poses.push_back(
            !world_t_opengl.isNull()
                ? PoseSampleFromOpenGLTransform(world_t_opengl, false)
                : PoseSample{});

        if (!frame.has_rgb_frame() || !frame.has_depth_frame() || !frame.has_camera_pose() ||
            !frame.has_camera_intrinsics()) {
          continue;
        }

        cv::Mat rgb = DecodeRgbFrame(frame);
        if (rgb.empty()) {
          continue;
        }

        cv::Mat depth = DecodeDepthFrame(frame, rgb.size());
        if (depth.empty()) {
          continue;
        }

        rtabmap::SensorData data(
            rgb,
            depth,
            BuildCameraModel(frame, rgb.size()),
            static_cast<int>(i + 1),
            frame.frame_identifier().timestamp_ns() > 0
                ? static_cast<double>(frame.frame_identifier().timestamp_ns()) / 1e9
                : static_cast<double>(i));

        // Inject IMU data (gravity + gyroscope + accelerometer + magnetometer).
        if (config.use_imu_gravity) {
          rtabmap::IMU imu = BuildIMU(frame);
          if (!imu.empty()) {
            data.setIMU(imu);
          }
        }

        // Inject GPS priors (latitude, longitude, altitude, accuracy, bearing).
        if (config.use_gps_priors) {
          rtabmap::GPS gps = BuildGPS(frame);
          if (gps.stamp() > 0.0) {
            data.setGPS(gps);
          }
        }

        const rtabmap::Transform world_t_rtab = FramePoseWorldToRtabmap(frame);
        if (world_t_rtab.isNull()) {
          continue;
        }

        if (!rtabmap.process(data, world_t_rtab)) {
          throw std::runtime_error("RTAB-Map failed while processing a frame.");
        }
        ++processed_frames;

        // Record which node ID corresponds to this frame index.
        const rtabmap::Memory * memory = rtabmap.getMemory();
        if (memory) {
          const int key = memory->getLastSignatureId();
          if (key > 0) {
            node_to_frame[key] = i;
          }
        }
      }
    }  // input closed here — all frame image memory freed

    if (processed_frames == 0) {
      throw std::runtime_error("RTAB-Map backend could not find any RGB-D frames to process.");
    }

    const std::size_t total_frames = frame_index;
    std::cout << "[rtabmap] [" << log_elapsed() << "] read " << total_frames
              << " frames, " << processed_frames << " accepted by SLAM\n";

    // ── Graph optimization ──────────────────────────────────────────────────
    std::cout << "[rtabmap] [" << log_elapsed() << "] optimizing pose graph...\n";
    std::cout.flush();

    std::map<int, rtabmap::Transform> optimized_poses;
    std::multimap<int, rtabmap::Link> constraints;
    rtabmap.getGraph(
        optimized_poses,
        constraints,
        true,
        true);

    std::cout << "[rtabmap] [" << log_elapsed() << "] optimized "
              << optimized_poses.size() << " nodes, "
              << CountLoopClosures(constraints) << " loop closures\n";

    for (std::size_t i = 0; i < total_frames; ++i) {
      const int id = static_cast<int>(i + 1);
      const auto pose_iter = optimized_poses.find(id);
      if (pose_iter == optimized_poses.end()) {
        continue;
      }
      const rtabmap::Transform world_t_opengl =
          pose_iter->second * rtabmap::Transform::rtabmap_T_opengl();
      output.poses[i] = PoseSampleFromOpenGLTransform(world_t_opengl, true);
    }

    output.loop_closures = CountLoopClosures(constraints);

    // Free RTAB-Map state before pass 2 to reclaim memory.
    rtabmap.close(false);

    // ── Pass 2: re-read keyframes one at a time, build cloud incrementally ──
    // Map frame index → (node_id, optimized pose) for needed keyframes.
    std::unordered_map<std::size_t, std::pair<int, rtabmap::Transform>> frame_to_pose;
    for (const auto & pose_entry : optimized_poses) {
      const auto it = node_to_frame.find(pose_entry.first);
      if (it != node_to_frame.end()) {
        frame_to_pose[it->second] = {pose_entry.first, pose_entry.second};
      }
    }
    node_to_frame.clear();  // no longer needed

    const std::size_t needed_count = frame_to_pose.size();
    std::cout << "[rtabmap] [" << log_elapsed() << "] pass 2/2: re-reading "
              << needed_count << " keyframes for point cloud...\n";
    std::cout.flush();

    const int decimation = config.quality == "fast" ? 4 : 2;
    const float voxel_size = config.quality == "fast" ? 0.08f : 0.05f;

    // Process one keyframe at a time — only one frame's images in memory.
    auto merged_cloud = pcl::PointCloud<pcl::PointXYZRGB>::Ptr(
        new pcl::PointCloud<pcl::PointXYZRGB>());
    {
      auto input = open_recording();
      std::size_t fi = 0;
      std::size_t loaded = 0;
      last_progress_time = std::chrono::steady_clock::now();
      while (input.good() && loaded < needed_count) {
        bayesmech::vision::PerceiverDataFrame frame;
        if (!ReadDelimitedMessage(input, frame)) {
          break;
        }
        const auto pose_it = frame_to_pose.find(fi);
        if (pose_it == frame_to_pose.end()) {
          ++fi;
          continue;
        }

        const auto now = std::chrono::steady_clock::now();
        if (std::chrono::duration<double>(now - last_progress_time).count() >= 2.0 || loaded == 0) {
          std::cout << "[rtabmap] [" << log_elapsed() << "] keyframe "
                    << (loaded + 1) << "/" << needed_count
                    << " | cloud=" << merged_cloud->size() << " pts\n";
          std::cout.flush();
          last_progress_time = now;
        }

        cv::Mat rgb = DecodeRgbFrame(frame);
        cv::Mat depth = rgb.empty() ? cv::Mat() : DecodeDepthFrame(frame, rgb.size());
        if (!rgb.empty() && !depth.empty()) {
          rtabmap::SensorData sd(
              rgb, depth,
              BuildCameraModel(frame, rgb.size()),
              pose_it->second.first, 0.0);
          auto cloud = rtabmap::util3d::cloudRGBFromSensorData(
              sd, decimation, 8.0f, 0.2f);
          if (cloud && !cloud->empty()) {
            cloud = rtabmap::util3d::transformPointCloud(
                cloud, pose_it->second.second);
            *merged_cloud += *cloud;
          }
        }
        ++loaded;
        ++fi;
      }
    }

    if (voxel_size > 0.0f && !merged_cloud->empty()) {
      std::cout << "[rtabmap] [" << log_elapsed() << "] voxelizing "
                << merged_cloud->size() << " points...\n";
      std::cout.flush();
      merged_cloud = rtabmap::util3d::voxelize(merged_cloud, voxel_size);
    }
    output.points = ConvertCloudToSamples(merged_cloud);

    std::cout << "[rtabmap] [" << log_elapsed() << "] done — "
              << output.points.size() << " cloud points\n";

    output.processing_time_s = std::chrono::duration<double>(
        std::chrono::steady_clock::now() - start_time).count();
    return output;
#else
    (void)recording_path;
    (void)max_frames;
    (void)config;
    throw std::runtime_error(
        "RTAB-Map backend requested, but native dependencies are missing. "
        "Install RTAB-Map and OpenCV to use this backend.");
#endif
  }
};

}  // namespace

std::unique_ptr<SlamBackend> CreateRtabmapBackend() {
  return std::make_unique<RtabmapBackend>();
}

}  // namespace mapmind
