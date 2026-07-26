const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const {
  addDeviceToProject,
  createProject,
  createControlProject,
  readControlProject,
  renameProject,
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
    ['Control'],
  )

  const withPhone = addDeviceToProject(recordingPath, 'phone_camera')
  assert.equal(withPhone.recordings.length, 1)
  assert.deepEqual(
    withPhone.recordings[0].controlProject.devices.map((device) => device.deviceType),
    ['ROBOT_CAR_DEVICE', 'PHONE_DEVICE'],
  )
  assert.deepEqual(
    withPhone.recordings[0].analyses.map((analysis) => analysis.title),
    ['Control', 'Video Phone'],
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
    ['Control', 'Video Phone'],
  )
})

test('robot preset keeps car video in Control and adds augmented video tabs', (t) => {
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
  ])

  const manifest = readControlProject(recording.controlProject.manifestPath)
  const phone = manifest.devices.find((device) => device.deviceType === 'PHONE_DEVICE')
  fs.closeSync(fs.openSync(path.join(manifest.directoryPath, phone.recordingFile), 'wx'))

  const rescanned = scanProject(recording.directoryPath)
  assert.equal(rescanned.recordings.length, 1)
  assert.deepEqual(rescanned.recordings[0].analyses.map((analysis) => analysis.title), [
    'Control',
    'Video Phone',
  ])
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
