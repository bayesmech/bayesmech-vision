const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const {
  getVggtResponseType,
  persistWorldgenComputation,
  worldgenPreviewFromMessages,
  worldgenSplatPaths,
} = require('./main.cjs')

function floatBuffer(values) {
  const buffer = Buffer.alloc(values.length * 4)
  values.forEach((value, index) => buffer.writeFloatLE(value, index * 4))
  return buffer
}

function computation(type, requestId, frameNumber, point, splatJobId) {
  return type.create({
    requestId,
    frameCount: 1,
    startFrameIndex: frameNumber,
    endFrameIndex: frameNumber,
    cameras: [{
      frameIdentifier: { frameNumber },
      sourceFrameIndex: frameNumber,
      cameraCenter: [point[0], point[1], point[2]],
    }],
    pointClouds: [{
      frameIdentifier: { frameNumber },
      sourceFrameIndex: frameNumber,
      pointCount: 1,
      returnedPointCount: 1,
      xyzF32Le: floatBuffer(point),
      rgbF32Le: floatBuffer([0.2, 0.4, 0.6]),
      uvF32Le: floatBuffer([1, 1]),
      confidenceF32Le: floatBuffer([0.9]),
    }],
    gaussianSplat: {
      status: 'complete',
      jobId: splatJobId,
      gaussianCount: 1,
      previewPointCount: 1,
      previewPoints: [{
        x: point[0],
        y: point[1],
        z: point[2],
        r: 0.2,
        g: 0.4,
        b: 0.6,
        opacity: 0.8,
        scale: 0.02,
      }],
    },
  })
}

test('marker-range computations stack in one world model with isolated splat artifacts', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'bayesmech-worldgen-'))
  try {
    const recordingPath = path.join(directory, 'recording.vis.pb')
    const outputPath = path.join(directory, 'recording.vggt.pb')
    const type = getVggtResponseType()
    const first = computation(type, 'request-a-b', 10, [1, 2, 3], 'splat-one')
    const second = computation(type, 'request-c-d', 20, [4, 5, 6], 'splat-two')

    persistWorldgenComputation(recordingPath, outputPath, first)
    const history = persistWorldgenComputation(recordingPath, outputPath, second)
    const combined = worldgenPreviewFromMessages(history, outputPath)

    assert.equal(history.length, 2)
    assert.equal(combined.frameCount, 2)
    assert.equal(combined.returnedPointCount, 2)
    assert.equal(combined.splatPoints.length, 2)
    assert.equal(combined.splat?.gaussianCount, 2)
    assert.equal(fs.existsSync(outputPath), true)

    const firstPaths = worldgenSplatPaths(outputPath, 'splat-one')
    const secondPaths = worldgenSplatPaths(outputPath, 'splat-two')
    assert.notEqual(firstPaths.plyPath, secondPaths.plyPath)
    assert.match(firstPaths.plyPath, /recording\.splats[\\/]splat-one[\\/]model\.splat\.ply$/)
    assert.match(secondPaths.previewPath, /recording\.splats[\\/]splat-two[\\/]preview\.json$/)
  } finally {
    fs.rmSync(directory, { recursive: true, force: true })
  }
})
