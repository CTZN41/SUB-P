import test from 'node:test'
import assert from 'node:assert/strict'
import { saveEmailReportConsent } from '../src/auth/accountProfile.ts'

test('email consent saves explicit choice for the current account and validates the saved response', async () => {
  const original = globalThis.fetch
  const calls = []
  try {
    globalThis.fetch = async (url, options) => {
      calls.push({ url, options })
      return Response.json({ id: 'account-a', email_reports_consent: JSON.parse(options.body).enabled })
    }
    assert.equal((await saveEmailReportConsent('account-a', true)).email_reports_consent, true)
    assert.equal((await saveEmailReportConsent('account-a', false)).email_reports_consent, false)
    assert.equal(calls[0].url, '/api/auth/profile/email-preference')
    assert.equal(calls[0].options.headers['X-Account-Id'], 'account-a')
    assert.deepEqual(JSON.parse(calls[1].options.body), { enabled: false })
    globalThis.fetch = async () => Response.json({ id: 'other', email_reports_consent: true })
    await assert.rejects(saveEmailReportConsent('account-a', true), error => error.code === 'session_changed')
    globalThis.fetch = async () => Response.json({ id: 'account-a', email_reports_consent: false })
    await assert.rejects(saveEmailReportConsent('account-a', true), error => error.code === 'email_consent_unconfirmed')
  } finally { globalThis.fetch = original }
})
