import test from 'node:test'
import assert from 'node:assert/strict'
import { shotCategory, shotSummary, shotNumbers, formatShotTime, parseShotTime, shouldShowPlayerNames } from '../src/utils/shotPresentation.ts'

const shot = (id, result, x = 25, y = 22) => ({ id, result, court_x: x, court_y: y })

test('display uses minutes and whole seconds without changing stored milliseconds', () => {
  assert.equal(formatShotTime(57444), '00:57')
  assert.equal(formatShotTime(506100), '08:26')
  assert.equal(formatShotTime(598349), '09:58')
  assert.equal(formatShotTime(599999), '09:59')
  assert.equal(formatShotTime(600000), '10:00')
  assert.equal(formatShotTime(NaN), '--:--')
})

test('84 records partition into exclusive outcome/position buckets', () => {
  const shots = [
    ...Array.from({ length: 68 }, (_, i) => shot(`made${i}`, 'made')),
    ...Array.from({ length: 11 }, (_, i) => shot(`miss${i}`, 'missed')),
    ...Array.from({ length: 3 }, (_, i) => shot(`position${i}`, 'made', null, null)),
    shot('positionMiss', 'missed', null, null), shot('bothMissing', 'needs_review', null, null),
  ]
  const counts = shotSummary(shots)
  assert.deepEqual(counts, { total: 84, made: 68, missed: 11, position_review: 4, outcome_review: 1 })
  assert.equal(counts.total, counts.made + counts.missed + counts.position_review + counts.outcome_review)
})

test('confirming one field does not prematurely count a shot as success', () => {
  const before = shot('a', 'needs_review', null, null)
  assert.equal(shotCategory(before), 'outcome_review')
  assert.equal(shotCategory({ ...before, result: 'made' }), 'position_review')
  assert.equal(shotCategory({ ...before, court_x: 25, court_y: 22 }), 'outcome_review')
  assert.equal(shotCategory({ ...before, result: 'made', court_x: 25, court_y: 22 }), 'made')
  assert.equal(shotCategory({ ...before, result: 'missed', court_x: 25, court_y: 22 }), 'missed')
})

test('invalid coordinates remain position review, valid edge coordinates count', () => {
  for (const [x, y] of [[NaN, 1], [51, 1], [1, 48], [-1, 1]]) {
    assert.equal(shotCategory(shot('a', 'made', x, y)), 'position_review')
  }
  assert.equal(shotCategory(shot('a', 'made', 0, 47)), 'made')
})

test('numbers come from the unfiltered source and remain after selection filters', () => {
  const shots = [shot('a', 'made'), shot('b', 'missed'), shot('c', 'made')]
  const numbers = shotNumbers(shots)
  assert.deepEqual(shots.filter(s => s.result === 'made').map(s => numbers[s.id]), [1, 3])
  assert.deepEqual(shotNumbers([]), {})
  assert.equal(shotSummary([]).total, 0)
})


test('default solo name is hidden but actual or multiple player names are shown', () => {
  assert.equal(shouldShowPlayerNames('solo', ['선수 A']), false)
  assert.equal(shouldShowPlayerNames('solo', ['민수']), true)
  assert.equal(shouldShowPlayerNames('solo', ['선수 A', '선수 B']), true)
  assert.equal(shouldShowPlayerNames('one_on_one', ['선수 A']), true)
})


test('minute inputs parse safely and preserve untouched subsecond timing', () => {
  assert.equal(parseShotTime('08:26'), 506000)
  assert.equal(parseShotTime('08:26', 506098), 506098)
  assert.equal(parseShotTime('08:27', 506098), 507000)
  assert.equal(parseShotTime('00:00'), 0)
  for (const invalid of ['08:60', '506.10', '08:26.10', '', '-1:00', '08:2']) assert.equal(parseShotTime(invalid), null)
})
