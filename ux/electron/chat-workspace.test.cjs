const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const test = require('node:test')

const {
  createChatSession,
  deleteChatSession,
  loadChatWorkspace,
} = require('./main.cjs')

test('saved chats can be deleted, including the final chat', (t) => {
  const recordingRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'bayesmech-chat-delete-'))
  const recordingPath = path.join(recordingRoot, 'recording.vis.pb')
  fs.closeSync(fs.openSync(recordingPath, 'wx'))
  const videoId = `chat-delete-${process.pid}-${Date.now()}`
  const workspaceDirectory = path.join(os.homedir(), '.bayesmech', videoId)
  t.after(() => {
    fs.rmSync(recordingRoot, { recursive: true, force: true })
    fs.rmSync(workspaceDirectory, { recursive: true, force: true })
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
})
