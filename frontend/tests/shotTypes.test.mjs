import test from 'node:test'
import assert from 'node:assert/strict'
import { shotTypeStatistics } from '../src/utils/shotTypes.ts'

test('type comparison uses known outcomes even when shot position is missing', () => {
  const rows = shotTypeStatistics([
    { shot_type: 'step_back', result: 'made', court_x: null },
    { shot_type: 'step_back', result: 'missed', court_x: 10 },
    { shot_type: 'step_back', result: 'needs_review', court_x: 10 },
    { shot_type: 'layup', result: 'made', court_x: 20 },
  ])
  assert.deepEqual(rows[0], { type: 'step_back', attempts: 3, made: 1, missed: 1, unresolved: 1, successRate: 50 })
  assert.equal(rows[1].successRate, 100)
})

test('unresolved and legacy shot types remain visible without inventing a rate', () => {
  assert.deepEqual(shotTypeStatistics([]), [])
  const rows = shotTypeStatistics([{ shot_type: 'needs_review', result: 'needs_review' }])
  assert.equal(rows[0].successRate, null)
  assert.equal(rows[0].attempts, 1)
  assert.equal(rows[0].unresolved, 1)
})
