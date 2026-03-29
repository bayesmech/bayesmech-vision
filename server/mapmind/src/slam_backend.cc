#include "slam_backend.h"

#include <stdexcept>

namespace mapmind {

std::unique_ptr<SlamBackend> CreateRtabmapBackend();

std::unique_ptr<SlamBackend> CreateBackend(const std::string & backend_name) {
  if (backend_name == "rtabmap") {
    return CreateRtabmapBackend();
  }

  throw std::invalid_argument("Unknown mapmind backend: " + backend_name);
}

}  // namespace mapmind
