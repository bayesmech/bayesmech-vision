const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const protobuf = require('protobufjs')
const test = require('node:test')

const {
  createChatSession,
  deleteChatSession,
  loadChatWorkspace,
  loadDesktopWorkspaceState,
  loadPersistentProjectState,
  readChatThread,
  saveChatSession,
  saveDesktopWorkspaceState,
  savePersistentProjectState,
  workspaceVideoDirectory,
} = require('./main.cjs')

const insightgenRoot = new protobuf.Root()
const protoDirectory = path.resolve(__dirname, '..', '..', 'proto')
insightgenRoot.resolvePath = (_origin, target) => path.join(protoDirectory, target)
insightgenRoot.loadSync(['insightgen.proto'], { keepCase: false })
insightgenRoot.resolveAll()
const chatHistoryType = insightgenRoot.lookupType('bayesmech.vision.ChatHistory')
const gensparkResponseType = insightgenRoot.lookupType('bayesmech.vision.GensparkResponse')

const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'bayesmech-state-'))
const previousStateHome = process.env.BAYESMECH_STATE_HOME
process.env.BAYESMECH_STATE_HOME = stateRoot
test.after(() => {
  if (previousStateHome === undefined) delete process.env.BAYESMECH_STATE_HOME
  else process.env.BAYESMECH_STATE_HOME = previousStateHome
  fs.rmSync(stateRoot, { recursive: true, force: true })
})

test('saved chats can be deleted, including the final chat', (t) => {
  const recordingRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'bayesmech-chat-delete-'))
  const recordingPath = path.join(recordingRoot, 'recording.vis.pb')
  fs.closeSync(fs.openSync(recordingPath, 'wx'))
  const videoId = `chat-delete-${process.pid}-${Date.now()}`
  t.after(() => {
    fs.rmSync(recordingRoot, { recursive: true, force: true })
  })

  const initial = loadChatWorkspace(videoId, recordingPath)
  const initialChatId = initial.activeChatId
  const withSecondChat = createChatSession(videoId, recordingPath)
  assert.equal(withSecondChat.chats.length, 2)

  const afterFirstDelete = deleteChatSession(
    videoId,
    recordingPath,
    initialChatId,
  )
  assert.equal(afterFirstDelete.chats.length, 1)
  assert.equal(afterFirstDelete.activeChatId, withSecondChat.activeChatId)

  const empty = deleteChatSession(
    videoId,
    recordingPath,
    withSecondChat.activeChatId,
  )
  assert.deepEqual(empty.chats, [])
  assert.equal(empty.activeChatId, '')
  assert.deepEqual(loadChatWorkspace(videoId, recordingPath).chats, [])

  const recreated = createChatSession(videoId, recordingPath)
  assert.equal(recreated.chats.length, 1)
  assert.ok(recreated.activeChatId)
  const active = recreated.chats[0]
  assert.equal(active.videoContext, 'main')
  saveChatSession(videoId, recordingPath, { ...active, videoContext: 'pov' })
  assert.equal(
    loadChatWorkspace(videoId, recordingPath).chats[0].videoContext,
    'pov',
  )
})

test('workspace and project state survive closing and reopening a project', (t) => {
  const firstRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'bayesmech-workspace-a-'))
  const secondRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'bayesmech-workspace-b-'))
  const recordingPath = path.join(firstRoot, 'recording.vis.pb')
  fs.closeSync(fs.openSync(recordingPath, 'wx'))
  t.after(() => {
    fs.rmSync(firstRoot, { recursive: true, force: true })
    fs.rmSync(secondRoot, { recursive: true, force: true })
  })

  saveDesktopWorkspaceState([firstRoot, secondRoot], firstRoot)
  savePersistentProjectState(firstRoot, {
    activeRecordingPath: recordingPath,
    activeChatId: 'chat-kept',
  })

  assert.deepEqual(loadDesktopWorkspaceState().loadedProjectPaths, [firstRoot, secondRoot])
  assert.equal(loadDesktopWorkspaceState().activeProjectPath, firstRoot)
  assert.equal(loadPersistentProjectState(firstRoot).activeRecordingPath, recordingPath)
  assert.equal(loadPersistentProjectState(firstRoot).activeChatId, 'chat-kept')

  saveDesktopWorkspaceState([secondRoot], secondRoot)
  assert.deepEqual(loadDesktopWorkspaceState().loadedProjectPaths, [secondRoot])
  assert.equal(loadPersistentProjectState(firstRoot).activeRecordingPath, recordingPath)

  saveDesktopWorkspaceState([secondRoot, firstRoot], firstRoot)
  assert.deepEqual(loadDesktopWorkspaceState().loadedProjectPaths, [secondRoot, firstRoot])
  assert.equal(loadPersistentProjectState(firstRoot).activeChatId, 'chat-kept')
})

test('same-named recordings keep chats isolated by project', (t) => {
  const firstRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'bayesmech-chat-project-a-'))
  const secondRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'bayesmech-chat-project-b-'))
  const firstRecording = path.join(firstRoot, 'recording.vis.pb')
  const secondRecording = path.join(secondRoot, 'recording.vis.pb')
  fs.closeSync(fs.openSync(firstRecording, 'wx'))
  fs.closeSync(fs.openSync(secondRecording, 'wx'))
  t.after(() => {
    fs.rmSync(firstRoot, { recursive: true, force: true })
    fs.rmSync(secondRoot, { recursive: true, force: true })
  })

  const first = createChatSession(firstRecording, firstRecording)
  const second = loadChatWorkspace(secondRecording, secondRecording)

  assert.equal(first.chats.length, 2)
  assert.equal(second.chats.length, 1)
  assert.notEqual(
    workspaceVideoDirectory(firstRecording, firstRecording),
    workspaceVideoDirectory(secondRecording, secondRecording),
  )
  assert.equal(
    loadPersistentProjectState(firstRoot).recordings[0].recordingPath,
    firstRecording,
  )
  assert.equal(
    loadPersistentProjectState(secondRoot).recordings[0].recordingPath,
    secondRecording,
  )
})

test('legacy flat chat state migrates without deleting its source', (t) => {
  const recordingRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'bayesmech-chat-migrate-'))
  const recordingPath = path.join(recordingRoot, 'capture.vis.pb')
  fs.closeSync(fs.openSync(recordingPath, 'wx'))
  const legacyDirectory = path.join(stateRoot, 'legacy-capture')
  const chatDirectory = path.join(legacyDirectory, 'chat-legacy')
  fs.mkdirSync(chatDirectory, { recursive: true })
  fs.writeFileSync(path.join(legacyDirectory, 'video.json'), JSON.stringify({
    version: 1,
    videoId: 'legacy-capture',
    recordingPath,
    activeChatId: 'chat-legacy',
    chatOrder: ['chat-legacy'],
  }))
  fs.writeFileSync(path.join(chatDirectory, 'meta.json'), JSON.stringify({
    version: 1,
    id: 'chat-legacy',
    title: 'Migrated chat',
    createdAt: '2026-07-26T00:00:00.000Z',
    updatedAt: '2026-07-26T00:00:00.000Z',
  }))
  fs.writeFileSync(path.join(chatDirectory, 'chat.json'), JSON.stringify({
    version: 1,
    messages: [{
      id: 'message-legacy',
      role: 'user',
      text: 'Keep this message',
      createdAt: '2026-07-26T00:00:00.000Z',
    }],
  }))
  fs.writeFileSync(path.join(chatDirectory, 'markers.json'), JSON.stringify({
    version: 1,
    markers: [],
  }))
  t.after(() => fs.rmSync(recordingRoot, { recursive: true, force: true }))

  const migrated = loadChatWorkspace(recordingPath, recordingPath)
  assert.equal(migrated.activeChatId, 'chat-legacy')
  assert.equal(migrated.chats[0].messages[0].text, 'Keep this message')
  assert.ok(fs.existsSync(path.join(legacyDirectory, 'video.json')))
  assert.ok(fs.existsSync(path.join(
    workspaceVideoDirectory(recordingPath, recordingPath),
    'chat-legacy',
    'chat.json',
  )))
})

test('protobuf chat and complete Genspark trace migrate into rich project chat state', (t) => {
  const recordingRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'bayesmech-rich-chat-'))
  const recordingPath = path.join(recordingRoot, 'capture.vis.pb')
  const basePath = recordingPath.slice(0, -'.vis.pb'.length)
  fs.closeSync(fs.openSync(recordingPath, 'wx'))
  const longToolResult = JSON.stringify({
    points: Array.from({ length: 3000 }, (_, index) => ({ index, value: index / 10 })),
  })
  fs.writeFileSync(
    `${basePath}.genspark.pb`,
    gensparkResponseType.encode(gensparkResponseType.fromObject({
      summary: {
        title: 'Complete trace',
        text: 'Short summary.',
        parameters: [],
      },
      turns: [{
        text: 'Full reasoning with `inline code` and:\n\n```python\nprint(\"hello\")\n```',
        toolCalls: [{
          toolName: 'inspect_points',
          argumentsJson: '{"limit":3000}',
          result: longToolResult,
        }],
      }],
    })).finish(),
  )
  fs.writeFileSync(
    `${basePath}.chat.pb`,
    chatHistoryType.encode(chatHistoryType.fromObject({
      fileName: path.basename(recordingPath),
      turns: [
        { role: 'user', text: 'What did you find?', timestampNs: '1000000000' },
        { role: 'model', text: 'The complete answer.', timestampNs: '2000000000' },
      ],
    })).finish(),
  )
  t.after(() => fs.rmSync(recordingRoot, { recursive: true, force: true }))

  const workspace = loadChatWorkspace(recordingPath, recordingPath)
  assert.equal(workspace.chats.length, 1)
  assert.equal(workspace.chats[0].source, 'legacy-chat-pb')
  assert.equal(workspace.chats[0].messages[0].text, 'What did you find?')
  assert.equal(workspace.activeChatId, workspace.chats[0].id)

  const thread = readChatThread(recordingPath)
  assert.equal(thread.analysis.turns.length, 1)
  assert.match(thread.analysis.turns[0].text, /```python/)
  assert.deepEqual(thread.analysis.turns[0].toolCalls[0].arguments, { limit: 3000 })
  assert.equal(thread.analysis.turns[0].toolCalls[0].result.length, longToolResult.length)

  const chat = workspace.chats[0]
  saveChatSession(recordingPath, recordingPath, {
    ...chat,
    messages: [
      ...chat.messages,
      {
        id: 'assistant-with-tool',
        role: 'assistant',
        text: 'Used a tool.',
        createdAt: '2026-07-26T00:00:00.000Z',
        toolCalls: [{
          name: 'inspect_points',
          arguments: { limit: 2 },
          result: { ok: true },
        }],
      },
    ],
  })
  const reloaded = loadChatWorkspace(recordingPath, recordingPath)
  assert.deepEqual(
    reloaded.chats[0].messages.at(-1).toolCalls,
    [{
      name: 'inspect_points',
      arguments: { limit: 2 },
      result: { ok: true },
    }],
  )
})
