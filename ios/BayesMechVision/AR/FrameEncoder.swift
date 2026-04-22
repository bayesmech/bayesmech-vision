import ARKit
import CoreImage
import Foundation
import simd
import UIKit

final class FrameEncoder {
    private let ciContext = CIContext()

    func encode(
        frame: ARFrame,
        planeAnchors: [ARPlaneAnchor],
        sensorSnapshot: SensorSnapshot,
        frameNumber: Int,
        deviceID: String,
        quality: QualityLevel,
        includeRGB: Bool,
        includeDepth: Bool,
        includeGeometry: Bool
    ) throws -> PerceiverDataFrame {
        var message = PerceiverDataFrame()

        let timestampNs = Int64(frame.timestamp * 1_000_000_000)

        var identifier = PerceiverFrameIdentifier()
        identifier.timestampNs = timestampNs
        identifier.frameNumber = UInt32(frameNumber)
        identifier.deviceID = deviceID

        message.frameIdentifier = identifier
        message.deviceTimestampNs = timestampNs
        message.cameraPose = makePose(from: frame.camera.transform)

        let rgbFrame = includeRGB ? try encodeRGBFrame(frame.capturedImage, jpegQuality: quality.jpegQuality) : nil
        let depthFrame = includeDepth ? encodeDepthFrame(from: frame) : nil

        message.cameraIntrinsics = makeIntrinsics(
            camera: frame.camera,
            rgbSize: rgbFrame.map { CGSize(width: Int($0.width), height: Int($0.height)) },
            depthSize: depthFrame.map { CGSize(width: Int($0.width), height: Int($0.height)) }
        )

        if let rgbFrame {
            message.rgbFrame = rgbFrame
        }
        if let depthFrame {
            message.depthFrame = depthFrame
        }
        if let imu = sensorSnapshot.toImuData() {
            message.imuData = imu
        }
        if includeGeometry {
            message.inferredGeometry = makeGeometry(frame: frame, planeAnchors: planeAnchors)
        }
        if let gps = sensorSnapshot.gpsLocation {
            message.gpsLocation = gps
        }

        return message
    }

    private func encodeRGBFrame(_ pixelBuffer: CVPixelBuffer, jpegQuality: Int) throws -> ImageFrame {
        let ciImage = CIImage(cvPixelBuffer: pixelBuffer)
        let rect = CGRect(x: 0, y: 0, width: CVPixelBufferGetWidth(pixelBuffer), height: CVPixelBufferGetHeight(pixelBuffer))
        guard let cgImage = ciContext.createCGImage(ciImage, from: rect) else {
            throw NSError(domain: "FrameEncoder", code: -1, userInfo: [NSLocalizedDescriptionKey: "Failed to create CGImage from camera frame"])
        }

        let image = UIImage(cgImage: cgImage)
        guard let jpegData = image.jpegData(compressionQuality: CGFloat(jpegQuality) / 100.0) else {
            throw NSError(domain: "FrameEncoder", code: -1, userInfo: [NSLocalizedDescriptionKey: "Failed to encode JPEG"])
        }

        var frame = ImageFrame()
        frame.data = jpegData
        frame.format = .jpeg
        frame.width = UInt32(cgImage.width)
        frame.height = UInt32(cgImage.height)
        frame.quality = UInt32(jpegQuality)
        return frame
    }

    private func encodeDepthFrame(from frame: ARFrame) -> DepthFrame? {
        guard let depthMap = (frame.sceneDepth ?? frame.smoothedSceneDepth)?.depthMap else {
            return nil
        }

        CVPixelBufferLockBaseAddress(depthMap, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(depthMap, .readOnly) }

        let width = CVPixelBufferGetWidth(depthMap)
        let height = CVPixelBufferGetHeight(depthMap)
        let rowStride = CVPixelBufferGetBytesPerRow(depthMap) / MemoryLayout<Float32>.size
        guard let baseAddress = CVPixelBufferGetBaseAddress(depthMap) else { return nil }

        var output = Data(count: width * height * MemoryLayout<UInt16>.size)
        output.withUnsafeMutableBytes { destinationBuffer in
            let destination = destinationBuffer.bindMemory(to: UInt16.self)
            let source = baseAddress.assumingMemoryBound(to: Float32.self)

            for y in 0..<height {
                for x in 0..<width {
                    let meters = source[(y * rowStride) + x]
                    let millimeters = UInt16(max(0, min(65535, Int(meters * 1000.0))))
                    destination[(y * width) + x] = millimeters.littleEndian
                }
            }
        }

        var depthFrame = DepthFrame()
        depthFrame.data = output
        depthFrame.format = .uint16Millimeters
        depthFrame.width = UInt32(width)
        depthFrame.height = UInt32(height)
        return depthFrame
    }

    private func makePose(from transform: simd_float4x4) -> Pose {
        let translation = transform.columns.3
        let rotation = simd_quatf(transform)

        var pose = Pose()
        var position = Vector3()
        position.x = translation.x
        position.y = translation.y
        position.z = translation.z
        pose.position = position

        var quaternion = Quaternion()
        quaternion.x = rotation.imag.x
        quaternion.y = rotation.imag.y
        quaternion.z = rotation.imag.z
        quaternion.w = rotation.real
        pose.rotation = quaternion

        return pose
    }

    private func makeIntrinsics(camera: ARCamera, rgbSize: CGSize?, depthSize: CGSize?) -> CameraIntrinsics {
        let intrinsics = camera.intrinsics
        let sourceWidth = Float(camera.imageResolution.width)
        let sourceHeight = Float(camera.imageResolution.height)
        let rgbWidth = Float(rgbSize?.width ?? CGFloat(sourceWidth))
        let rgbHeight = Float(rgbSize?.height ?? CGFloat(sourceHeight))
        let depthWidth = Float(depthSize?.width ?? .zero)
        let depthHeight = Float(depthSize?.height ?? .zero)

        let scaleX = sourceWidth > 0 ? rgbWidth / sourceWidth : 1
        let scaleY = sourceHeight > 0 ? rgbHeight / sourceHeight : 1

        var output = CameraIntrinsics()
        output.fx = intrinsics.columns.0.x * scaleX
        output.fy = intrinsics.columns.1.y * scaleY
        output.cx = intrinsics.columns.2.x * scaleX
        output.cy = intrinsics.columns.2.y * scaleY
        output.imageWidth = rgbWidth
        output.imageHeight = rgbHeight
        output.depthWidth = depthWidth
        output.depthHeight = depthHeight
        return output
    }

    private func makeGeometry(frame: ARFrame, planeAnchors: [ARPlaneAnchor]) -> InferredGeometry {
        var geometry = InferredGeometry()

        if let rawFeaturePoints = frame.rawFeaturePoints {
            for point in rawFeaturePoints.points {
                var trackedPoint = InferredGeometry.TrackedPoint()
                var position = Vector3()
                position.x = point.x
                position.y = point.y
                position.z = point.z
                trackedPoint.point = position
                trackedPoint.confidence = 1
                geometry.pointCloud.append(trackedPoint)
            }
        }

        for planeAnchor in planeAnchors {
            var plane = InferredGeometry.Plane()
            plane.type = planeAnchor.alignment == .vertical ? .vertical : .horizontalUpwardFacing
            plane.extentX = planeAnchor.extent.x
            plane.extentZ = planeAnchor.extent.z
            plane.centerPose = makePose(from: planeAnchor.transform)

            let boundaryVertices = planeAnchor.geometry.boundaryVertices
            let boundaryCount = Int(planeAnchor.geometry.boundaryVertexCount)
            for index in 0..<boundaryCount {
                let vertex = boundaryVertices[index]
                var point = Vector3()
                point.x = vertex.x
                point.y = 0
                point.z = vertex.z
                plane.polygon.append(point)
            }
            geometry.planes.append(plane)
        }

        return geometry
    }
}
