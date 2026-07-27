const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const protobuf = require('protobufjs')
const test = require('node:test')

const {
  readDomainReconstruction,
  readDomainTriangulation,
  scanProject,
} = require('./main.cjs')

const protoDirectory = path.resolve(__dirname, '..', '..', 'proto')

function protoType(fileName, messageName) {
  const root = new protobuf.Root()
  root.resolvePath = (_origin, target) => path.join(protoDirectory, target)
  root.loadSync([fileName], { keepCase: false })
  root.resolveAll()
  return root.lookupType(messageName)
}

function record(type, value) {
  const payload = Buffer.from(type.encode(type.fromObject(value)).finish())
  const header = Buffer.alloc(4)
  header.writeUInt32BE(payload.length)
  return Buffer.concat([header, payload])
}

test('Pongtown reconstruction exposes table, net, trajectory, bounces, and frame quads', (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'bayesmech-pong-domain-'))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  const base = 'capture'
  fs.closeSync(fs.openSync(path.join(root, `${base}.vis.pb`), 'wx'))
  const type = protoType('pongtown.proto', 'bayesmech.vision.PongtownResponse')
  const filePath = path.join(root, `${base}.pongtown.pb`)
  fs.writeFileSync(filePath, Buffer.concat([
    record(type, {
      frameIdentifier: { frameNumber: 42, timestampNs: '42000000' },
      sportMode: 'PINGPONG',
      pingpongTracking: {
        ballPositions: [{
          frameIdx: 0,
          frameNumber: 42,
          hasTablePosition: true,
          tableXyzMm: [-250, 120, 0],
          insideTable: true,
        }],
      },
      frameOutput: {
        frameIdx: 0,
        hasPose: true,
        globalIou: 0.91,
        tableQuadImg: [10, 20, 110, 20, 110, 70, 10, 70],
        netQuadImg: [58, 18, 62, 18, 62, 72, 58, 72],
      },
    }),
    record(type, {
      frameIdentifier: { frameNumber: 43, timestampNs: '43000000' },
      sportMode: 'PINGPONG',
      pingpongTracking: {
        ballPositions: [{
          frameIdx: 1,
          frameNumber: 43,
          hasTablePosition: true,
          tableXyzMm: [50, -80, 0],
          insideTable: true,
        }],
      },
      frameOutput: {
        frameIdx: 1,
        hasPose: true,
        globalIou: 0.89,
        tableQuadImg: [12, 21, 112, 21, 112, 71, 12, 71],
        netQuadImg: [59, 19, 63, 19, 63, 73, 59, 73],
      },
    }),
    record(type, {
      sportMode: 'PINGPONG',
      globalTablePose: { hasPose: true, hasNetPose: true, meanIou: 0.88 },
      tableWidthMm: 2740,
      tableHeightMm: 1525,
      netHeightMm: 152.5,
      netOverhangMm: 152.5,
      pingpongTracking: {
        ballTrajectory: {
          positions: [
            {
              frameIdx: 0,
              frameNumber: 42,
              hasTablePosition: true,
              tableXyzMm: [-250, 120, 0],
              insideTable: true,
            },
            {
              frameIdx: 1,
              frameNumber: 43,
              hasTablePosition: true,
              tableXyzMm: [50, -80, 0],
              insideTable: true,
            },
          ],
          bounces: [{
            frameIdx: 1,
            frameNumber: 43,
            hasTablePosition: true,
            tableXyzMm: [50, -80, 0],
            insideTable: true,
            confidence: 0.94,
          }],
        },
      },
    }),
  ]))

  const project = scanProject(root)
  const analysis = project.recordings[0].analyses.find((item) => item.key === 'pongtown')
  assert.equal(analysis.title, 'Domain specific reconstruction')

  const reconstruction = readDomainReconstruction(filePath)
  assert.equal(reconstruction.sportMode, 'PINGPONG')
  assert.equal(reconstruction.hasNet, true)
  assert.equal(reconstruction.trajectory.length, 2)
  assert.equal(reconstruction.bounces.length, 1)
  assert.equal(reconstruction.frames.length, 2)
  assert.equal(reconstruction.frames[0].frameIndex, 0)
  assert.equal(reconstruction.frames[0].frameNumber, 42)
  assert.equal(reconstruction.frames[0].balls[0].xMm, -250)
  assert.equal(reconstruction.frames[1].frameIndex, 1)
  assert.equal(reconstruction.frames[1].balls[0].xMm, 50)
  assert.equal(reconstruction.trajectory[1].frameIndex, 1)
  assert.equal(reconstruction.bounces[0].frameIndex, 1)
  assert.deepEqual(
    [reconstruction.bounces[0].xMm, reconstruction.bounces[0].yMm],
    [50, -80],
  )

  const triangulation = readDomainTriangulation(filePath, 42)
  assert.deepEqual(triangulation.tableQuad, [10, 20, 110, 20, 110, 70, 10, 70])
  assert.deepEqual(triangulation.netQuad, [58, 18, 62, 18, 62, 72, 58, 72])
})

test('Pongtown reconstruction recovers drop locations from an older trajectory without bounces', (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'bayesmech-pong-drops-'))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  const type = protoType('pongtown.proto', 'bayesmech.vision.PongtownResponse')
  const filePath = path.join(root, 'capture.pongtown.pb')
  const imageY = [0, 1, 3, 8, 12, 8, 3, 1, 0]
  fs.writeFileSync(filePath, record(type, {
    sportMode: 'PINGPONG',
    globalTablePose: { hasPose: true, hasNetPose: true },
    tableWidthMm: 2740,
    tableHeightMm: 1525,
    pingpongTracking: {
      ballTrajectory: {
        minBounceProminencePx: 2,
        minBounceSpacingFrames: 4,
        smoothSigma: 1,
        positions: imageY.map((vImg, frameIdx) => ({
          observationIdx: frameIdx,
          frameIdx,
          frameNumber: 100 + frameIdx,
          vImg,
          hasTablePosition: true,
          tableXyzMm: [frameIdx * 10, frameIdx * -5, 0],
          insideTable: true,
        })),
      },
    },
  }))

  const reconstruction = readDomainReconstruction(filePath)
  assert.equal(reconstruction.bounces.length, 1)
  assert.equal(reconstruction.bounces[0].label, 'Drop 1')
  assert.equal(reconstruction.bounces[0].frameIndex, 4)
  assert.equal(reconstruction.bounces[0].frameNumber, 104)
  assert.deepEqual(
    [reconstruction.bounces[0].xMm, reconstruction.bounces[0].yMm],
    [40, -20],
  )
})

test('Snookerstown reconstruction exposes every frame while retaining the latest summary', (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'bayesmech-snooker-domain-'))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  const type = protoType('snookestown.proto', 'bayesmech.vision.SnookerResponse')
  const filePath = path.join(root, 'capture.snook.pb')
  fs.writeFileSync(filePath, Buffer.concat([
    record(type, {
      frameIdentifier: { frameNumber: 100, timestampNs: '100000000' },
      tablePose: {
        homographyImgToTableMm: [1, 0, 0, 0, 1, 0, 0, 0, 1],
        method: 'POCKET_FULL',
        quality: 0.9,
      },
      balls: [
        { trackId: 1, xMm: -100, yMm: 25, confidence: 0.8 },
        { trackId: 2, xMm: 200, yMm: -50, confidence: 0.7 },
      ],
    }),
    record(type, {
      frameIdentifier: { frameNumber: 101, timestampNs: '101000000' },
      tablePose: {
        homographyImgToTableMm: [1, 0, 0, 0, 1, 0, 0, 0, 1],
        method: 'POSE_PROPAGATED',
        quality: 0.75,
      },
      balls: [{ trackId: 1, xMm: -80, yMm: 35, confidence: 0.85 }],
    }),
    record(type, {
      tracks: [
        { trackId: 1, color: 'WHITE' },
        { trackId: 2, color: 'RED' },
      ],
      tableWidthMm: 3569,
      tableHeightMm: 1778,
      canonicalPockets: [{ xMm: -1784.5, yMm: -889, kind: 'CORNER' }],
      totalFrames: 2,
    }),
  ]))

  const reconstruction = readDomainReconstruction(filePath)
  assert.equal(reconstruction.sportMode, 'SNOOKER')
  assert.equal(reconstruction.balls.length, 2)
  assert.equal(reconstruction.balls.find((ball) => ball.label === 'WHITE').xMm, -80)
  assert.equal(reconstruction.balls.find((ball) => ball.label === 'RED').xMm, 200)
  assert.equal(reconstruction.pockets.length, 1)
  assert.equal(reconstruction.frames.length, 2)
  assert.deepEqual(
    reconstruction.frames[0].balls.map((ball) => [ball.label, ball.xMm]),
    [['WHITE', -100], ['RED', 200]],
  )
  assert.deepEqual(
    reconstruction.frames[1].balls.map((ball) => [ball.label, ball.xMm]),
    [['WHITE', -80]],
  )
  assert.equal(reconstruction.frames[1].frameIndex, 1)
  assert.equal(reconstruction.frames[1].frameNumber, 101)

  const triangulation = readDomainTriangulation(filePath, 101)
  assert.equal(triangulation.tableQuad.length, 8)
  assert.equal(triangulation.netQuad.length, 0)
  assert.equal(triangulation.method, 'POSE_PROPAGATED')
})
