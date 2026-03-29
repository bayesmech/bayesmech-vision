cmake_minimum_required(VERSION 3.20)

project(mapmind LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(MAPMIND_ENABLE_RTABMAP "Build the RTAB-Map backend when native dependencies are available." ON)

set(MAPMIND_LOCAL_VENDOR_BUILD_DIR "${CMAKE_SOURCE_DIR}/build/vendor")
set(MAPMIND_LOCAL_VENDOR_PREFIX_DIR "${CMAKE_SOURCE_DIR}/build/vendor-prefix")
set(MAPMIND_LOCAL_SYSTEM_PREFIX_DIR "${CMAKE_SOURCE_DIR}/build/system-prefix")
set(MAPMIND_LOCAL_PACKAGE_HINTS
  "${MAPMIND_LOCAL_SYSTEM_PREFIX_DIR}/rtabmap"
  "${MAPMIND_LOCAL_VENDOR_PREFIX_DIR}/rtabmap"
  "${MAPMIND_LOCAL_VENDOR_BUILD_DIR}/rtabmap"
)

foreach(package_hint IN LISTS MAPMIND_LOCAL_PACKAGE_HINTS)
  if(EXISTS "${package_hint}")
    list(APPEND CMAKE_PREFIX_PATH "${package_hint}")
  endif()
endforeach()

find_package(Protobuf REQUIRED)
find_package(yaml-cpp REQUIRED)
find_package(Boost REQUIRED COMPONENTS serialization)
find_package(OpenCV REQUIRED COMPONENTS core imgcodecs)

set(MAPMIND_HAVE_RTABMAP OFF)
set(MAPMIND_RTABMAP_INCLUDE_DIRS "")
set(MAPMIND_RTABMAP_LINK_LIBS "")

find_package(RTABMap QUIET CONFIG COMPONENTS core)

if(MAPMIND_ENABLE_RTABMAP)
  if(TARGET rtabmap::core)
    if(OpenCV_FOUND)
      set(MAPMIND_HAVE_RTABMAP ON)
      list(APPEND MAPMIND_RTABMAP_INCLUDE_DIRS ${RTABMap_INCLUDE_DIRS})
      list(APPEND MAPMIND_RTABMAP_LINK_LIBS rtabmap::core)
    endif()
  else()
  find_path(
    RTABMAP_INCLUDE_DIR
    rtabmap/core/Rtabmap.h
    HINTS
      "${CMAKE_SOURCE_DIR}/libs/rtabmap/corelib/include"
      "${MAPMIND_LOCAL_SYSTEM_PREFIX_DIR}/rtabmap/include"
      "${MAPMIND_LOCAL_SYSTEM_PREFIX_DIR}/rtabmap/include/rtabmap-0.23"
      "${MAPMIND_LOCAL_VENDOR_PREFIX_DIR}/rtabmap/include"
      "${MAPMIND_LOCAL_VENDOR_PREFIX_DIR}/rtabmap/include/rtabmap-0.23"
  )
  find_library(
    RTABMAP_CORE_LIBRARY
    rtabmap_core
    HINTS
      "${MAPMIND_LOCAL_SYSTEM_PREFIX_DIR}/rtabmap/lib"
      "${MAPMIND_LOCAL_VENDOR_BUILD_DIR}/rtabmap/corelib"
      "${MAPMIND_LOCAL_VENDOR_PREFIX_DIR}/rtabmap/lib"
  )
  endif()
endif()

if(NOT MAPMIND_HAVE_RTABMAP AND MAPMIND_ENABLE_RTABMAP AND RTABMAP_INCLUDE_DIR AND RTABMAP_CORE_LIBRARY AND OpenCV_FOUND)
  set(MAPMIND_HAVE_RTABMAP ON)
  list(APPEND MAPMIND_RTABMAP_INCLUDE_DIRS ${RTABMAP_INCLUDE_DIR})
  list(APPEND MAPMIND_RTABMAP_LINK_LIBS ${RTABMAP_CORE_LIBRARY})
elseif(MAPMIND_ENABLE_RTABMAP AND NOT MAPMIND_HAVE_RTABMAP)
  message(WARNING "RTAB-Map backend disabled: missing RTAB-Map and/or OpenCV native dependencies.")
endif()

set(MAPMIND_PROTO_DIR ${CMAKE_SOURCE_DIR}/../../proto)
set(MAPMIND_PROTO_FILES
  ${MAPMIND_PROTO_DIR}/primitives.proto
  ${MAPMIND_PROTO_DIR}/spatial.proto
  ${MAPMIND_PROTO_DIR}/perceiver.proto
  ${MAPMIND_PROTO_DIR}/mapmind.proto
)

protobuf_generate_cpp(MAPMIND_PROTO_SRCS MAPMIND_PROTO_HDRS ${MAPMIND_PROTO_FILES})

add_executable(mapmind
  src/main.cc
  src/config.cc
  src/protoio.cc
  src/slam_backend.cc
  src/rtabmap_backend.cc
  ${MAPMIND_PROTO_SRCS}
)

target_include_directories(mapmind PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/src
  ${CMAKE_CURRENT_BINARY_DIR}
  ${MAPMIND_PROTO_DIR}
)

target_compile_definitions(mapmind PRIVATE
  MAPMIND_HAVE_RTABMAP=$<BOOL:${MAPMIND_HAVE_RTABMAP}>
)

target_link_libraries(mapmind PRIVATE
  protobuf::libprotobuf
  yaml-cpp
)

if(MAPMIND_HAVE_RTABMAP)
  target_include_directories(mapmind PRIVATE ${MAPMIND_RTABMAP_INCLUDE_DIRS})
  target_link_libraries(mapmind PRIVATE ${MAPMIND_RTABMAP_LINK_LIBS} ${OpenCV_LIBS})
endif()
