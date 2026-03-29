#include "config.h"
#include "protoio.h"
#include "slam_backend.h"

#include "mapmind.pb.h"
#include "perceiver.pb.h"

#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

struct Args {
  std::filesystem::path recording;
  std::filesystem::path config_path;
  std::size_t max_frames = 0;
  std::string backend_override;
  std::string quality_override;
};

std::filesystem::path DefaultConfigPath(const std::filesystem::path & argv0) {
  return std::filesystem::absolute(argv0).parent_path().parent_path() / "mapmind_config.yaml";
}

std::filesystem::path OutputPath(const std::filesystem::path & recording) {
  const std::string name = recording.filename().string();
  const std::string suffix = ".vis.pb";
  if (name.size() >= suffix.size() &&
      name.compare(name.size() - suffix.size(), suffix.size(), suffix) == 0) {
    return recording.parent_path() / (name.substr(0, name.size() - suffix.size()) + ".mapmind.pb");
  }
  return recording.parent_path() / (recording.stem().string() + ".mapmind.pb");
}

std::filesystem::path PointCloudPath(const std::filesystem::path & recording) {
  const auto mapmind_path = OutputPath(recording);
  return mapmind_path.parent_path() / (mapmind_path.stem().string() + ".point_cloud.ply");
}

Args ParseArgs(int argc, char ** argv) {
  if (argc < 2) {
    throw std::invalid_argument(
        "Usage: mapmind <recording.vis.pb> [--config path] [--max-frames N] "
        "[--backend name] [--quality fast|default]");
  }

  Args args;
  args.recording = std::filesystem::absolute(argv[1]);
  args.config_path = DefaultConfigPath(argv[0]);

  for (int i = 2; i < argc; ++i) {
    const std::string arg = argv[i];
    if (arg == "--config" && i + 1 < argc) {
      args.config_path = std::filesystem::absolute(argv[++i]);
    } else if (arg == "--max-frames" && i + 1 < argc) {
      args.max_frames = static_cast<std::size_t>(std::stoull(argv[++i]));
    } else if (arg == "--backend" && i + 1 < argc) {
      args.backend_override = argv[++i];
    } else if (arg == "--quality" && i + 1 < argc) {
      args.quality_override = argv[++i];
    } else {
      throw std::invalid_argument("Unknown or incomplete argument: " + arg);
    }
  }

  return args;
}

void FillPose(
    const mapmind::PoseSample & sample,
    bayesmech::vision::Pose * pose) {
  pose->mutable_position()->set_x(static_cast<float>(sample.tx));
  pose->mutable_position()->set_y(static_cast<float>(sample.ty));
  pose->mutable_position()->set_z(static_cast<float>(sample.tz));
  pose->mutable_rotation()->set_x(static_cast<float>(sample.qx));
  pose->mutable_rotation()->set_y(static_cast<float>(sample.qy));
  pose->mutable_rotation()->set_z(static_cast<float>(sample.qz));
  pose->mutable_rotation()->set_w(static_cast<float>(sample.qw));
}

void WritePointCloudPly(
    const std::filesystem::path & path,
    const std::vector<mapmind::PointSample> & points) {
  std::ofstream output(path, std::ios::binary);
  if (!output) {
    throw std::runtime_error("Unable to open point cloud output: " + path.string());
  }

  output << "ply\n";
  output << "format binary_little_endian 1.0\n";
  output << "element vertex " << points.size() << '\n';
  output << "property float x\n";
  output << "property float y\n";
  output << "property float z\n";
  output << "property uchar red\n";
  output << "property uchar green\n";
  output << "property uchar blue\n";
  output << "end_header\n";

  for (const auto & point : points) {
    output.write(reinterpret_cast<const char *>(&point.x), sizeof(point.x));
    output.write(reinterpret_cast<const char *>(&point.y), sizeof(point.y));
    output.write(reinterpret_cast<const char *>(&point.z), sizeof(point.z));
    output.write(reinterpret_cast<const char *>(&point.r), sizeof(point.r));
    output.write(reinterpret_cast<const char *>(&point.g), sizeof(point.g));
    output.write(reinterpret_cast<const char *>(&point.b), sizeof(point.b));
  }
}

}  // namespace

int main(int argc, char ** argv) {
  GOOGLE_PROTOBUF_VERIFY_VERSION;

  try {
    const Args args = ParseArgs(argc, argv);
    mapmind::AppConfig config = mapmind::LoadConfig(args.config_path);
    if (!args.backend_override.empty()) {
      config.slam.backend = args.backend_override;
    }
    if (!args.quality_override.empty()) {
      config.slam.quality = args.quality_override;
    }

    const auto backend = mapmind::CreateBackend(config.slam.backend);
    const mapmind::SlamOutput slam = backend->Run(args.recording, args.max_frames, config.slam);

    const std::size_t num_frames = slam.poses.size();
    if (num_frames == 0) {
      throw std::runtime_error("SLAM backend produced no poses.");
    }

    const std::filesystem::path output_path = OutputPath(args.recording);
    if (std::filesystem::exists(output_path)) {
      std::filesystem::remove(output_path);
    }

    std::cout << "[mapmind] writing " << num_frames << " frame records...\n";
    std::vector<bayesmech::vision::MapMindRecord> batch;
    batch.reserve(50);

    for (std::size_t i = 0; i < num_frames; ++i) {
      bayesmech::vision::MapMindRecord record;
      auto * frame_out = record.mutable_frame();

      if (i < slam.frame_ids.size()) {
        frame_out->mutable_frame_identifier()->CopyFrom(slam.frame_ids[i]);
      } else {
        frame_out->mutable_frame_identifier()->set_frame_number(static_cast<std::uint32_t>(i));
      }

      FillPose(slam.poses[i], frame_out->mutable_slam_pose());
      frame_out->set_is_keyframe(slam.poses[i].is_keyframe);
      frame_out->set_confidence(slam.poses[i].is_keyframe ? 1.0f : 0.5f);

      batch.push_back(std::move(record));
      if (batch.size() >= 50) {
        mapmind::AppendMapMindRecords(output_path, batch);
        batch.clear();
      }
    }

    if (!batch.empty()) {
      mapmind::AppendMapMindRecords(output_path, batch);
    }

    bayesmech::vision::MapMindRecord result_record;
    auto * result = result_record.mutable_result();
    result->set_total_frames(static_cast<std::uint32_t>(num_frames));
    std::uint32_t keyframe_count = 0;
    for (const auto & pose : slam.poses) {
      if (pose.is_keyframe) {
        ++keyframe_count;
      }
    }
    result->set_keyframe_count(keyframe_count);
    result->set_processing_time_s(static_cast<float>(slam.processing_time_s));
    result->set_slam_backend(slam.backend);
    result->set_slam_config(slam.quality);
    result->set_loop_closures(slam.loop_closures);
    if (!slam.points.empty()) {
      auto * point_cloud = result->mutable_point_cloud();
      point_cloud->mutable_xs()->Reserve(static_cast<int>(slam.points.size()));
      point_cloud->mutable_ys()->Reserve(static_cast<int>(slam.points.size()));
      point_cloud->mutable_zs()->Reserve(static_cast<int>(slam.points.size()));
      point_cloud->mutable_rs()->Reserve(static_cast<int>(slam.points.size()));
      point_cloud->mutable_gs()->Reserve(static_cast<int>(slam.points.size()));
      point_cloud->mutable_bs()->Reserve(static_cast<int>(slam.points.size()));
      for (const auto & point : slam.points) {
        point_cloud->add_xs(point.x);
        point_cloud->add_ys(point.y);
        point_cloud->add_zs(point.z);
        point_cloud->add_rs(static_cast<std::uint32_t>(point.r));
        point_cloud->add_gs(static_cast<std::uint32_t>(point.g));
        point_cloud->add_bs(static_cast<std::uint32_t>(point.b));
      }
    }
    mapmind::AppendMapMindRecords(output_path, {result_record});

    if (!slam.points.empty()) {
      const std::filesystem::path point_cloud_path = PointCloudPath(args.recording);
      WritePointCloudPly(point_cloud_path, slam.points);
      std::cout << "[mapmind] wrote " << point_cloud_path.filename().string()
                << " with " << slam.points.size() << " points\n";
    }

    std::cout << "[mapmind] wrote " << output_path.filename().string()
              << " with " << num_frames << " frame records and 1 result record\n";
    return 0;
  } catch (const std::exception & e) {
    std::cerr << "[mapmind] error: " << e.what() << '\n';
    return 1;
  }
}
