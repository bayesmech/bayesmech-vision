const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const {
  getVggtResponseType,
  normalizeRunnerBackgroundJob,
  persistWorldgenComputation,
  setWorldgenResultPending,
  worldgenPreviewFromMessages,
  worldgenSplatPaths,
} = require('./main.cjs')

function floatBuffer(values) {
  const buffer = Buffer.alloc(values.length * 4)
  values.forEach((value, index) => buffer.writeFloatLE(value, index * 4))
  return buffer
}

function computation(type, requestId, frameNumber, point, splatJobId, transfer = {}) {
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
    resultFrameIndex: transfer.resultFrameIndex,
    resultFrameCount: transfer.resultFrameCount,
    resultComplete: transfer.resultComplete,
    runnerJobId: transfer.runnerJobId,
  })
}

test('marker-range computations stack in one world model with isolated splat artifacts', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'bayesmech-worldgen-'))
  try {
    const recordingPath = path.join(directory, 'recording.vis.pb')
    const outputPath = path.join(directory, 'recording.worldgen.pb')
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

test('VGGT background progress reaches completion only after the local result is received', (t) => {
  const jobId = 'vggt-local-lifecycle'
  setWorldgenResultPending(jobId)
  t.after(() => setWorldgenResultPending(jobId, false))

  const running = normalizeRunnerBackgroundJob({
    job_id: jobId,
    type: 'vggt',
    status: 'running',
    progress: 0.5,
  })
  assert.equal(running.status, 'running')
  assert.equal(running.progress, 0.45)

  const remoteComplete = normalizeRunnerBackgroundJob({
    job_id: jobId,
    type: 'vggt',
    status: 'complete',
    progress: 1,
  })
  assert.equal(remoteComplete.status, 'receiving')
  assert.equal(remoteComplete.stage, 'awaiting_result')
  assert.equal(remoteComplete.progress, 0.9)

  setWorldgenResultPending(jobId, false)
  const savedLocally = normalizeRunnerBackgroundJob({
    job_id: jobId,
    type: 'vggt',
    status: 'complete',
    stage: 'saved_local',
    progress: 1,
  })
  assert.equal(savedLocally.status, 'complete')
  assert.equal(savedLocally.progress, 1)
})

test('completed remote VGGT work stays below 100 percent until its request is in the local artifact', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'bayesmech-worldgen-progress-'))
  try {
    const recordingPath = path.join(directory, 'recording.vis.pb')
    const outputPath = path.join(directory, 'recording.vggt.pb')
    const type = getVggtResponseType()
    persistWorldgenComputation(
      recordingPath,
      outputPath,
      computation(type, 'saved-request', 5, [1, 2, 3], ''),
    )

    const saved = normalizeRunnerBackgroundJob({
      job_id: 'vggt-saved',
      type: 'vggt',
      status: 'complete',
      progress: 1,
      recording_path: recordingPath,
      request_id: 'saved-request',
    })
    assert.equal(saved.status, 'complete')
    assert.equal(saved.progress, 1)

    const notDownloaded = normalizeRunnerBackgroundJob({
      job_id: 'vggt-not-downloaded',
      type: 'vggt',
      status: 'complete',
      progress: 1,
      recording_path: recordingPath,
      request_id: 'another-request',
    })
    assert.equal(notDownloaded.status, 'receiving')
    assert.equal(notDownloaded.progress, 0.9)
  } finally {
    fs.rmSync(directory, { recursive: true, force: true })
  }
})

test('incremental Worldgen transfer is complete only after every frame is saved', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'bayesmech-worldgen-incremental-'))
  try {
    const recordingPath = path.join(directory, 'recording.vis.pb')
    const outputPath = path.join(directory, 'recording.worldgen.pb')
    const type = getVggtResponseType()
    const rawJob = {
      job_id: 'vggt-incremental',
      type: 'vggt',
      status: 'complete',
      progress: 1,
      recording_path: recordingPath,
      request_id: 'incremental-request',
    }

    persistWorldgenComputation(
      recordingPath,
      outputPath,
      computation(type, 'incremental-request', 10, [1, 2, 3], '', {
        resultFrameIndex: 0,
        resultFrameCount: 2,
        resultComplete: false,
        runnerJobId: 'vggt-incremental',
      }),
    )
    const partial = normalizeRunnerBackgroundJob(rawJob)
    assert.equal(partial.status, 'receiving')
    assert.equal(partial.progress, 0.9)

    persistWorldgenComputation(
      recordingPath,
      outputPath,
      computation(type, 'incremental-request', 11, [4, 5, 6], '', {
        resultFrameIndex: 1,
        resultFrameCount: 2,
        resultComplete: true,
        runnerJobId: 'vggt-incremental',
      }),
    )
    const complete = normalizeRunnerBackgroundJob(rawJob)
    assert.equal(complete.status, 'complete')
    assert.equal(complete.progress, 1)
  } finally {
    fs.rmSync(directory, { recursive: true, force: true })
  }
})
