const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const { scanProject, scanVisFiles } = require('./main.cjs')

function touch(filePath) {
  fs.writeFileSync(filePath, Buffer.alloc(0))
}

test('suffixed vis files become named contexts on their base recording', (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'bayesmech-video-contexts-'))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))

  const mainPath = path.join(root, 'snooker_topview.vis.pb')
  const povPath = path.join(root, 'snooker_topview_pov.vis.pb')
  const unrelatedPath = path.join(root, 'practice.vis.pb')
  touch(mainPath)
  touch(povPath)
  touch(unrelatedPath)
  touch(path.join(root, 'snooker_topview.seg.pb'))
  touch(path.join(root, 'snooker_topview_pov.seg.pb'))
  touch(path.join(root, 'snooker_topview_pov.snook.pb'))

  const project = scanProject(root)
  assert.equal(project.recordings.length, 2)
  const recording = project.recordings.find((item) => item.path === mainPath)
  assert.ok(recording)
  assert.deepEqual(
    recording.videoContexts.map((context) => ({
      name: context.name,
      path: context.path,
      main: context.isMain,
    })),
    [
      { name: 'main', path: mainPath, main: true },
      { name: 'pov', path: povPath, main: false },
    ],
  )
  assert.deepEqual(
    recording.analyses
      .filter((analysis) => ['video', 'video:pov', 'segmentation', 'segmentation:pov', 'snookestown:pov'].includes(analysis.key))
      .map((analysis) => ({
        key: analysis.key,
        title: analysis.title,
        context: analysis.videoContext,
        source: analysis.sourceVideoPath,
      })),
    [
      { key: 'video', title: 'Video', context: 'main', source: mainPath },
      { key: 'video:pov', title: 'Video', context: 'pov', source: povPath },
      { key: 'segmentation', title: 'Segmentation', context: 'main', source: mainPath },
      { key: 'segmentation:pov', title: 'Segmentation', context: 'pov', source: povPath },
      {
        key: 'snookestown:pov',
        title: 'Domain specific reconstruction',
        context: 'pov',
        source: povPath,
      },
    ],
  )
})

test('opening selected sibling files groups them the same way as a project scan', (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'bayesmech-selected-contexts-'))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))

  const mainPath = path.join(root, 'match.vis.pb')
  const phonePath = path.join(root, 'match_phone-camera.vis.pb')
  touch(mainPath)
  touch(phonePath)

  const project = scanVisFiles([phonePath, mainPath])
  assert.equal(project.recordings.length, 1)
  assert.deepEqual(
    project.recordings[0].videoContexts.map((context) => context.name),
    ['main', 'phone-camera'],
  )
})
