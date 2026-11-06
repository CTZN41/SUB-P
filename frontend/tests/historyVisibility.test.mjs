import test from 'node:test'
import assert from 'node:assert/strict'
import { visibleHistoryRecords } from '../src/utils/historyVisibility.ts'

test('presentation records stay below all real dates without changing the source', () => {
  const records = Object.freeze([
    { id: 'new-demo', is_demo: true }, { id: 'new-real', is_demo: false },
    { id: 'old-demo', is_demo: true }, { id: 'legacy-real' },
  ])
  assert.deepEqual(visibleHistoryRecords(records, false).map(r => r.id), ['new-real', 'legacy-real', 'new-demo', 'old-demo'])
  assert.equal(records[0].id, 'new-demo')
})

test('recent cards never fill empty slots with demos', () => {
  const records = [{ id: 'demo', is_demo: true }, { id: 'real' }]
  assert.deepEqual(visibleHistoryRecords(records, true).map(r => r.id), ['real'])
  assert.deepEqual(visibleHistoryRecords([records[0]], true), [])
})
