import test from 'node:test'
import assert from 'node:assert/strict'
import { safeAuthNext, authMessage } from '../src/auth/authPolicy.ts'

test('login never redirects outside the application or back into an auth loop', () => {
  for (const path of ['https://evil.example', '//evil.example', '/\\evil.example', '/login', '/signup', '/auth/callback', '/history\n?x', null]) assert.equal(safeAuthNext(path), '/')
  for (const path of ['/', '/new', '/history', '/history/monthly', '/results/test-id', '/progress/test-id']) assert.equal(safeAuthNext(path), path)
})

test('provider errors show safe Korean messages rather than raw credentials or server details', () => {
  assert.match(authMessage('invalid_credentials'), /이메일/)
  assert.match(authMessage('email_not_confirmed'), /인증/)
  assert.doesNotMatch(authMessage('private-provider-payload'), /private-provider-payload/)
})
