const assert = require('node:assert/strict')
const test = require('node:test')

const { buildGemmaSampleIndexes } = require('./main.cjs')

test('Gemma sampling targets four frames per second for short videos', () => {
  assert.deepEqual(
    buildGemmaSampleIndexes(31, 1, 4, 32),
    [0, 8, 15, 23, 30],
  )
  assert.deepEqual(
    buildGemmaSampleIndexes(16, 0.5, 4, 32),
    [0, 8, 15],
  )
})

test('Gemma sampling respects the 32-frame runner limit across the full video', () => {
  const indexes = buildGemmaSampleIndexes(1801, 60, 4, 32)

  assert.equal(indexes.length, 32)
  assert.equal(indexes[0], 0)
  assert.equal(indexes.at(-1), 1800)
  assert.ok(indexes.every((value, index) => index === 0 || value > indexes[index - 1]))
})

test('Gemma sampling uses a source-rate fallback when timestamps are absent', () => {
  assert.deepEqual(
    buildGemmaSampleIndexes(30, 0, 4, 32, 30),
    [0, 10, 19, 29],
  )
  assert.deepEqual(buildGemmaSampleIndexes(30, 1, 4, 0), [])
})
