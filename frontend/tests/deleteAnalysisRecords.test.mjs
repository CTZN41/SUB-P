import test from 'node:test'
import assert from 'node:assert/strict'
import { deleteAnalysisRecords } from '../src/utils/deleteAnalysisRecords.ts'

test('sends the unique selection in one request and one visible update', async () => {
  const requests = [], updates = []
  const result = await deleteAnalysisRecords(['a', 'b', 'a'], async ids => {
    requests.push(ids)
    return { removed: ids, failed: [] }
  }, ids => updates.push(ids))
  assert.deepEqual(requests, [['a', 'b']])
  assert.deepEqual(updates, [['a', 'b']])
  assert.deepEqual(result, { removed: ['a', 'b'], failed: [] })
})

test('keeps all records visible until the complete batch returns', async () => {
  let finish
  const updates = []
  const pending = deleteAnalysisRecords(['a', 'b'], () => new Promise(resolve => { finish = resolve }), ids => updates.push(ids))
  assert.deepEqual(updates, [])
  finish({ removed: ['a', 'b'], failed: [] })
  await pending
  assert.deepEqual(updates, [['a', 'b']])
})

test('keeps failures and retries only those IDs', async () => {
  const updates = []
  const first = await deleteAnalysisRecords(['a', 'busy', 'c'], async () => ({
    removed: ['a', 'c'], failed: [{ id: 'busy', message: '분석 중에는 삭제할 수 없습니다.' }],
  }), ids => updates.push(ids))
  assert.deepEqual(updates, [['a', 'c']])
  const requests = []
  await deleteAnalysisRecords(first.failed.map(item => item.id), async ids => {
    requests.push(ids)
    return { removed: ids, failed: [] }
  }, () => {})
  assert.deepEqual(requests, [['busy']])
})

test('empty selection never sends a request', async () => {
  assert.deepEqual(await deleteAnalysisRecords([], async () => { assert.fail('unexpected request') }, () => assert.fail('unexpected update')), { removed: [], failed: [] })
})

test('network errors preserve the visible records', async () => {
  await assert.rejects(deleteAnalysisRecords(['a', 'b'], async () => { throw new Error('connection') }, () => assert.fail('unexpected update')), /connection/)
})

test('incomplete or unrelated results cannot remove visible records', async () => {
  for (const result of [{ removed: ['a'], failed: [] }, { removed: ['a', 'other'], failed: [] }, { removed: ['a', 'a'], failed: [] }]) {
    await assert.rejects(deleteAnalysisRecords(['a', 'b'], async () => result, () => assert.fail('unexpected update')), /삭제 결과/)
  }
})
