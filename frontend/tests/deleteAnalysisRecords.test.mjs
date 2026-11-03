import test from 'node:test'
import assert from 'node:assert/strict'
import { deleteAnalysisRecords } from '../src/utils/deleteAnalysisRecords.ts'

test('only confirmed unique records are removed, once each, in order', async () => {
  const called = [], removedFromScreen = []
  const result = await deleteAnalysisRecords(['a', 'b', 'a'], async id => { called.push(id) }, id => removedFromScreen.push(id))
  assert.deepEqual(called, ['a', 'b'])
  assert.deepEqual(removedFromScreen, ['a', 'b'])
  assert.deepEqual(result, { removed: ['a', 'b'], failed: [] })
})

test('failed records remain available while other selected records complete', async () => {
  const removedFromScreen = []
  const result = await deleteAnalysisRecords(['a', 'busy', 'c'], async id => {
    if (id === 'busy') throw new Error('분석 중에는 삭제할 수 없습니다.')
  }, id => removedFromScreen.push(id))
  assert.deepEqual(removedFromScreen, ['a', 'c'])
  assert.deepEqual(result.removed, ['a', 'c'])
  assert.deepEqual(result.failed, [{ id: 'busy', message: '분석 중에는 삭제할 수 없습니다.' }])
})

test('empty selection never sends a delete request', async () => {
  let calls = 0
  const result = await deleteAnalysisRecords([], async () => { calls++ }, () => { calls++ })
  assert.equal(calls, 0)
  assert.deepEqual(result, { removed: [], failed: [] })
})

test('retry removes only previously failed IDs', async () => {
  const first = await deleteAnalysisRecords(['a', 'b'], async id => { if (id === 'b') throw new Error('연결 오류') }, () => {})
  const retried = []
  const second = await deleteAnalysisRecords(first.failed.map(item => item.id), async id => { retried.push(id) }, () => {})
  assert.deepEqual(retried, ['b'])
  assert.deepEqual(second, { removed: ['b'], failed: [] })
})
