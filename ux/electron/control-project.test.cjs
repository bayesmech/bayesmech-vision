const assert = require('node:assert/strict')
const fs = require('node:fs')
const http = require('node:http')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')
const protobuf = require('protobufjs')

const {
  addDeviceToProject,
  agentHarnessSystemContext,
  createProject,
  createControlProject,
  readControlProject,
  renameProject,
  runAgentChat,
  scanProject,
} = require('./main.cjs')

const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'bayesmech-control-state-'))
const previousStateHome = process.env.BAYESMECH_STATE_HOME
process.env.BAYESMECH_STATE_HOME = stateRoot
test.after(() => {
  if (previousStateHome === undefined) delete process.env.BAYESMECH_STATE_HOME
  else process.env.BAYESMECH_STATE_HOME = previousStateHome
  fs.rmSync(stateRoot, { recursive: true, force: true })
})

test('project display names persist without renaming their directories', (t) => {
  const recordingsRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'bayesmech-project-name-'))
  t.after(() => fs.rmSync(recordingsRoot, { recursive: true, force: true }))

  const created = createProject(recordingsRoot)
  const originalDirectory = created.rootPath
  const recordingPath = created.recordings[0].path
  const renamed = renameProject(recordingPath, 'Workshop Rover')
  assert.equal(renamed.rootPath, originalDirectory)
  assert.equal(renamed.name, 'Workshop Rover')
  assert.equal(renamed.recordings[0].displayName, 'Workshop Rover')

  addDeviceToProject(recordingPath, 'robot_car')
  const renamedControl = renameProject(recordingPath, 'Loading Bay Rover')
  assert.equal(renamedControl.rootPath, originalDirectory)
  assert.equal(renamedControl.recordings[0].displayName, 'Loading Bay Rover')
  assert.equal(
    readControlProject(renamedControl.recordings[0].controlProject.manifestPath).displayName,
    'Loading Bay Rover',
  )
})

test('devices attach to a regular project without changing its primary recording', (t) => {
  const recordingsRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'bayesmech-project-'))
  t.after(() => fs.rmSync(recordingsRoot, { recursive: true, force: true }))

  const created = createProject(recordingsRoot)
  assert.equal(created.recordings.length, 1)
  assert.match(path.basename(created.rootPath), /^\d{8}_\d{6}_project$/)
  const recordingPath = created.recordings[0].path
  assert.equal(created.recordings[0].controlProject, undefined)
  assert.equal(agentHarnessSystemContext(recordingPath), '')

  const withCar = addDeviceToProject(recordingPath, 'robot_car')
  assert.equal(withCar.recordings.length, 1)
  assert.equal(withCar.recordings[0].path, recordingPath)
  assert.equal(withCar.recordings[0].controlProject.projectType, 'ROBOT_CAR')
  assert.deepEqual(
    withCar.recordings[0].controlProject.devices.map((device) => ({
      id: device.deviceId,
      type: device.deviceType,
      role: device.role,
    })),
    [{ id: 'robocar-1', type: 'ROBOT_CAR_DEVICE', role: 'PRIMARY_DEVICE' }],
  )
  assert.deepEqual(
    withCar.recordings[0].analyses.map((analysis) => analysis.title),
    ['Control', 'Video'],
  )
  const robotContext = agentHarnessSystemContext(recordingPath)
  assert.match(robotContext, /camera and an ultrasonic distance sensor/)
  assert.match(robotContext, /four independently commanded wheel speeds/)
  assert.match(robotContext, /left front, right front, left back, and right back/)

  const withPhone = addDeviceToProject(recordingPath, 'phone_camera')
  assert.equal(withPhone.recordings.length, 1)
  assert.deepEqual(
    withPhone.recordings[0].controlProject.devices.map((device) => device.deviceType),
    ['ROBOT_CAR_DEVICE', 'PHONE_DEVICE'],
  )
  assert.deepEqual(
    withPhone.recordings[0].analyses.map((analysis) => analysis.title),
    ['Control', 'Video', 'Video'],
  )
  assert.deepEqual(
    withPhone.recordings[0].videoContexts.map((context) => context.name),
    ['main', 'phone'],
  )
  const phone = withPhone.recordings[0].controlProject.devices[1]
  assert.ok(fs.statSync(path.join(withPhone.rootPath, phone.recordingFile)).isFile())

  const withSecondCar = addDeviceToProject(recordingPath, 'robot_car')
  assert.deepEqual(
    withSecondCar.recordings[0].controlProject.devices.map((device) => device.deviceId),
    ['robocar-1', 'phone-1', 'robocar-2'],
  )
  assert.deepEqual(
    withSecondCar.recordings[0].analyses.map((analysis) => analysis.title),
    ['Control', 'Video', 'Video', 'Video'],
  )
  assert.deepEqual(
    withSecondCar.recordings[0].videoContexts.map((context) => context.name),
    ['main', 'phone', 'robot-car'],
  )
})

test('robot preset exposes primary and augmented streams as video tabs', (t) => {
  const recordingsRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'bayesmech-control-'))
  t.after(() => fs.rmSync(recordingsRoot, { recursive: true, force: true }))

  const created = createControlProject('robot_car', recordingsRoot)
  assert.equal(created.recordings.length, 1)
  const recording = created.recordings[0]
  assert.match(path.basename(recording.directoryPath), /^\d{8}_\d{6}_robot_car$/)
  assert.equal(recording.controlProject.projectType, 'ROBOT_CAR')
  assert.deepEqual(
    recording.controlProject.devices.map((device) => ({
      type: device.deviceType,
      role: device.role,
      controlPort: device.controlPort,
      streamPort: device.streamPort,
    })),
    [
      {
        type: 'ROBOT_CAR_DEVICE',
        role: 'PRIMARY_DEVICE',
        controlPort: 80,
        streamPort: 81,
      },
      {
        type: 'PHONE_DEVICE',
        role: 'AUGMENTED_DEVICE',
        controlPort: 0,
        streamPort: 8080,
      },
    ],
  )
  assert.deepEqual(recording.analyses.map((analysis) => analysis.title), [
    'Control',
    'Video',
  ])

  const manifest = readControlProject(recording.controlProject.manifestPath)
  const phone = manifest.devices.find((device) => device.deviceType === 'PHONE_DEVICE')
  fs.closeSync(fs.openSync(path.join(manifest.directoryPath, phone.recordingFile), 'wx'))

  const rescanned = scanProject(recording.directoryPath)
  assert.equal(rescanned.recordings.length, 1)
  assert.deepEqual(rescanned.recordings[0].analyses.map((analysis) => analysis.title), [
    'Control',
    'Video',
    'Video',
  ])
  assert.deepEqual(
    rescanned.recordings[0].videoContexts.map((context) => context.name),
    ['main', 'phone'],
  )
})

test('fixed augmented cameras retain their manifest display name as the video context', (t) => {
  const recordingsRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'bayesmech-top-camera-'))
  t.after(() => fs.rmSync(recordingsRoot, { recursive: true, force: true }))

  const created = createControlProject('robot_car', recordingsRoot)
  const manifestPath = created.recordings[0].controlProject.manifestPath
  const protoRoot = new protobuf.Root()
  const protoDirectory = path.resolve(__dirname, '../../proto')
  protoRoot.resolvePath = (_origin, target) => path.join(protoDirectory, target)
  protoRoot.loadSync(['control.proto'], { keepCase: false })
  protoRoot.resolveAll()
  const projectType = protoRoot.lookupType('bayesmech.vision.ControlProject')
  const manifest = projectType.decode(fs.readFileSync(manifestPath))
  const topCamera = manifest.devices.find((device) => device.deviceType === 4)
  topCamera.deviceId = 'top-camera'
  topCamera.displayName = 'Top Camera'
  topCamera.recordingFile = `${manifest.projectId}.top_camera.vis.pb`
  fs.writeFileSync(manifestPath, projectType.encode(manifest).finish())
  fs.closeSync(fs.openSync(path.join(created.rootPath, topCamera.recordingFile), 'wx'))

  const rescanned = scanProject(created.rootPath)
  assert.deepEqual(
    rescanned.recordings[0].videoContexts.map((context) => context.name),
    ['main', 'top-camera'],
  )
  assert.equal(
    rescanned.recordings[0].analyses.find((analysis) => analysis.key === 'video:top-camera')?.title,
    'Video',
  )
})

test('control projects expose project-local vis assets missing from an older manifest', (t) => {
  const recordingsRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'bayesmech-control-assets-'))
  t.after(() => fs.rmSync(recordingsRoot, { recursive: true, force: true }))

  const created = createProject(recordingsRoot)
  const recordingPath = created.recordings[0].path
  const withCar = addDeviceToProject(recordingPath, 'robot_car')
  const projectId = withCar.recordings[0].controlProject.projectId
  const topCameraPath = path.join(withCar.rootPath, `${projectId}_top_camera.vis.pb`)
  fs.closeSync(fs.openSync(topCameraPath, 'wx'))

  const rescanned = scanProject(withCar.rootPath)
  assert.equal(rescanned.recordings.length, 1)
  assert.deepEqual(
    rescanned.recordings[0].videoContexts.map((context) => context.name),
    ['main', 'top-camera'],
  )
  assert.deepEqual(
    rescanned.recordings[0].analyses.map((analysis) => analysis.title),
    ['Control', 'Video', 'Video'],
  )
})

test('all control presets create timestamped projects', (t) => {
  const recordingsRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'bayesmech-presets-'))
  t.after(() => fs.rmSync(recordingsRoot, { recursive: true, force: true }))

  for (const preset of ['robot_car', 'robot_hand', 'drone_control']) {
    const project = createControlProject(preset, recordingsRoot)
    assert.equal(project.recordings.length, 1)
    assert.match(path.basename(project.rootPath), new RegExp(`^\\d{8}_\\d{6}_${preset}`))
    assert.ok(project.recordings[0].controlProject)
  }
})

test('empty control recordings start text chat with robot harness context', async (t) => {
  const recordingsRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'bayesmech-empty-chat-'))
  const project = createControlProject('robot_car', recordingsRoot)
  const recordingPath = project.recordings[0].path
  assert.equal(fs.statSync(recordingPath).size, 0)

  let submittedBody = ''
  const server = http.createServer((request, response) => {
    const sendJson = (status, value) => {
      response.writeHead(status, { 'Content-Type': 'application/json' })
      response.end(JSON.stringify(value))
    }
    if (request.method === 'POST' && request.url === '/api/v1/agent/jobs') {
      const chunks = []
      request.on('data', (chunk) => chunks.push(chunk))
      request.on('end', () => {
        submittedBody = Buffer.concat(chunks).toString('utf8')
        sendJson(202, { job_id: 'gemma-empty-test' })
      })
      return
    }
    if (request.url === '/api/v1/agent/jobs/gemma-empty-test/result') {
      sendJson(200, {
        text: 'Ready without video.',
        model: 'test-gemma',
        sampled_frame_count: 0,
        tool_calls: [],
      })
      return
    }
    if (request.url === '/api/v1/agent/jobs/gemma-empty-test') {
      sendJson(200, { status: 'complete' })
      return
    }
    sendJson(404, { detail: 'not found' })
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()
  const previousEndpoint = process.env.RUNNER_ENDPOINT
  process.env.RUNNER_ENDPOINT = `http://127.0.0.1:${address.port}`
  t.after(() => {
    if (previousEndpoint === undefined) delete process.env.RUNNER_ENDPOINT
    else process.env.RUNNER_ENDPOINT = previousEndpoint
    server.close()
    fs.rmSync(recordingsRoot, { recursive: true, force: true })
  })

  const result = await runAgentChat({
    requestId: 'request-empty',
    recordingPath,
    chatId: 'chat-empty',
    message: 'What hardware is available?',
    history: [],
  })

  assert.equal(result.sampledFrameCount, 0)
  assert.equal(result.text, 'Ready without video.')
  assert.doesNotMatch(submittedBody, /filename=/)
  assert.match(submittedBody, /camera and an ultrasonic distance sensor/)
  assert.match(submittedBody, /four independently commanded wheel speeds/)
})
