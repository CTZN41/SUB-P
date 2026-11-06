import test from 'node:test'
import assert from 'node:assert/strict'
import { canSendSelectedReports } from '../src/utils/historyReports.ts'

test('report selection requires every record to be ready', () => {
  assert.equal(canSendSelectedReports([]), false)
  assert.equal(canSendSelectedReports([{ report_ready: true }]), true)
  assert.equal(canSendSelectedReports([{ report_ready: true }, { report_ready: true }]), true)
  for (const incomplete of [{ report_ready: false, position_pending: 1 }, { report_ready: false, outcome_pending: 1 }, {}]) {
    assert.equal(canSendSelectedReports([incomplete]), false)
    assert.equal(canSendSelectedReports([{ report_ready: true }, incomplete]), false)
  }
})
