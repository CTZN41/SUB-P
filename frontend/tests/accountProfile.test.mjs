import test from 'node:test'
import assert from 'node:assert/strict'
import { normalizeNickname, accountNickname, registerNickname, registerPassword,
  profileProviderLabels, accountUpdateMessage, profileNickname, claimSuggestedNickname,
  registerEmail, AccountUpdateError, checkSignup, claimNickname } from '../src/auth/accountProfile.ts'

const user = { id: 'account-a', email: 'first@example.test', email_confirmed_at: '2026-11-04T01:00:00Z', user_metadata: {} }
const profile = { id: user.id, nickname: '', email: user.email, email_confirmed: true, providers: ['google'], password_registered: false, can_register_password: true }
function mockAuth(overrides = {}) {
  const calls = []
  return { calls, auth: {
    getUser: async () => ({ data: { user }, error: null }),
    updateUser: async attributes => { calls.push(attributes); return { data: { user: { ...user, user_metadata: attributes.data ?? {} } }, error: null } },
    ...overrides,
  } }
}

test('nickname input accepts trimmed Korean and rejects missing, control, HTML and oversized values', () => {
  assert.equal(normalizeNickname('  슈터 A_1  '), '슈터 A_1')
  for (const value of ['', 'a', 'a'.repeat(21), '<script>', 'a\nb', null, {}]) assert.equal(normalizeNickname(value), '')
  assert.equal(accountNickname({ nickname: 'Google name' }), '')
  assert.equal(accountNickname({ shotlog_nickname: '슈터 A' }), '슈터 A')
})

test('nickname registration uses an authenticated server claim and verifies durable state', async () => {
  const { auth, calls } = mockAuth()
  const claimed = []
  const saved = { ...profile, nickname: '슈터 A' }
  await registerNickname(auth, user.id, '  슈터 A ', async () => saved, async nickname => { claimed.push(nickname); return saved })
  assert.deepEqual(claimed, ['슈터 A'])
  assert.deepEqual(calls, [])
})

test('a saved server nickname survives a new login with missing provider/session metadata', () => {
  assert.equal(accountNickname({ name: 'Provider name' }), '')
  assert.equal(profileNickname({ ...profile, nickname: '저장된닉네임' }, user.id), '저장된닉네임')
  assert.equal(profileNickname({ ...profile, nickname: '다른계정' }, 'account-b'), '')
  assert.equal(profileNickname(null, user.id), '')
})

test('nickname success requires matching persisted server state, not just an SDK success', async () => {
  const first = mockAuth()
  const claim = async () => ({ ...profile, nickname: '슈터 A' })
  await assert.rejects(registerNickname(first.auth, user.id, '슈터 A', async () => profile, claim), { code: 'nickname_unconfirmed' })
  await assert.rejects(registerNickname(first.auth, user.id, '슈터 A', async () => ({ ...profile, id: 'account-b', nickname: '슈터 A' }), claim), { code: 'session_changed' })
})

test('invalid or stale-account nicknames never update and legacy metadata does not block choosing a new name', async () => {
  const first = mockAuth()
  await assert.rejects(registerNickname(first.auth, user.id, '<script>'), { code: 'nickname_invalid' })
  await assert.rejects(registerNickname(first.auth, 'account-b', '슈터 A'), { code: 'session_changed' })
  assert.deepEqual(first.calls, [])
  const existing = mockAuth({ getUser: async () => ({ data: { user: { ...user, user_metadata: { shotlog_nickname: '기존닉네임' } } }, error: null }) })
  const saved = { ...profile, nickname: '다른닉네임' }
  await registerNickname(existing.auth, user.id, '다른닉네임', async () => saved, async () => saved)
  assert.deepEqual(existing.calls, [])
})

test('nickname normalization prevents compatibility/case/spacing bypass; conflicting suggestion is never reclaimed', async () => {
  assert.equal(normalizeNickname(' Ａ  player '), 'A player')
  let called = false
  const old = { ...profile, nickname_suggestion: 'Taken name', nickname_conflict: true }
  assert.equal(await claimSuggestedNickname(old, async () => { called = true }), old)
  assert.equal(called, false)
  const empty = { ...profile, nickname_suggestion: 'New name' }
  const saved = { ...profile, nickname: 'New name' }
  assert.deepEqual(await claimSuggestedNickname(empty, async () => saved), saved)
  const raced = { ...empty, nickname_conflict: true }
  assert.deepEqual(await claimSuggestedNickname(empty, async () => { throw new AccountUpdateError('nickname_taken') }, async () => raced), raced)
})

test('signup and nickname API requests carry no password/token and duplicate errors remain actionable', async () => {
  const original = globalThis.fetch
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    return { ok: false, json: async () => ({ detail: { code: 'EMAIL_TAKEN', message: 'private-data' } }) }
  }
  try {
    await assert.rejects(checkSignup('Player', ' DUP@example.test '), { code: 'email_taken' })
    assert.deepEqual(JSON.parse(calls[0].options.body), { nickname: 'Player', email: 'dup@example.test' })
    assert.equal(calls[0].options.credentials, 'same-origin')
    await assert.rejects(claimNickname('Player', user.id), { code: 'email_taken' })
    assert.equal(calls[1].options.headers['X-Account-Id'], user.id)
    assert.match(accountUpdateMessage(new AccountUpdateError('email_taken')), /사용 중인 이메일/)
  } finally { globalThis.fetch = original }
})

test('email registration checks duplicate availability and requests verification without claiming confirmation', async () => {
  const calls = []
  const emailUser = { ...user, email: undefined, email_confirmed_at: undefined }
  const { auth } = mockAuth({ getUser: async () => ({ data: { user: emailUser }, error: null }),
    updateUser: async (attributes, options) => { calls.push({ attributes, options }); return { data: { user: { ...emailUser, new_email: attributes.email } }, error: null } } })
  const empty = { ...profile, email: null, email_confirmed: false, can_register_email: true }
  const checked = []
  const pending = await registerEmail(auth, user.id, ' NEW@example.test ', 'http://127.0.0.1:5173', async () => empty, async email => checked.push(email))
  assert.deepEqual(checked, ['new@example.test'])
  assert.deepEqual(calls, [{ attributes: { email: 'new@example.test' }, options: { emailRedirectTo: 'http://127.0.0.1:5173/auth/callback' } }])
  assert.equal(pending.email_confirmed_at, undefined)
  assert.equal(pending.new_email, 'new@example.test')
})

test('invalid, duplicate, already registered and stale-account emails do not reach provider updates', async () => {
  const missing = mockAuth({ getUser: async () => ({ data: { user: { ...user, email: undefined } }, error: null }) })
  const empty = { ...profile, email: null, can_register_email: true }
  const args = [missing.auth, user.id, 'new@example.test', 'http://127.0.0.1:5173']
  await assert.rejects(registerEmail(missing.auth, user.id, 'bad', args[3]), { code: 'email_invalid' })
  await assert.rejects(registerEmail(missing.auth, 'account-b', args[2], args[3]), { code: 'session_changed' })
  await assert.rejects(registerEmail(...args, async () => profile), { code: 'email_exists' })
  await assert.rejects(registerEmail(...args, async () => empty, async () => { throw new AccountUpdateError('email_taken') }), { code: 'email_taken' })
  assert.deepEqual(missing.calls, [])
})

test('provider uniqueness recheck failure is never reported as a successful email request', async () => {
  const first = mockAuth({ getUser: async () => ({ data: { user: { ...user, email: undefined } }, error: null }),
    updateUser: async () => ({ data: { user: null }, error: { code: 'email_exists', message: 'private-data' } }) })
  await assert.rejects(registerEmail(first.auth, user.id, 'new@example.test', 'http://127.0.0.1:5173',
    async () => ({ ...profile, email: null, can_register_email: true }), async () => {}), { code: 'email_exists' })
})

test('password registration uses the signed-in user password API and does not change identity or metadata', async () => {
  const { auth, calls } = mockAuth()
  await registerPassword(auth, user.id, 'Test-only-pass-2026', 'Test-only-pass-2026', async () => profile)
  assert.deepEqual(calls, [{ password: 'Test-only-pass-2026' }])
  assert.equal(profileProviderLabels({ ...profile, password_registered: true }).join(','), 'Google,이메일·비밀번호')
})

test('mismatched and short passwords never reach the provider', async () => {
  const { auth, calls } = mockAuth()
  await assert.rejects(registerPassword(auth, user.id, 'short', 'short'), { code: 'password_length' })
  await assert.rejects(registerPassword(auth, user.id, 'long-enough', 'different'), { code: 'password_mismatch' })
  assert.deepEqual(calls, [])
})

test('stale sessions and unverified email never update a password', async () => {
  const first = mockAuth()
  await assert.rejects(registerPassword(first.auth, 'account-b', 'test-pass-123', 'test-pass-123'), { code: 'session_changed' })
  assert.deepEqual(first.calls, [])
  const unverified = mockAuth({ getUser: async () => ({ data: { user: { ...user, email_confirmed_at: null } }, error: null }) })
  await assert.rejects(registerPassword(unverified.auth, user.id, 'test-pass-123', 'test-pass-123'), { code: 'email_not_confirmed' })
  assert.deepEqual(unverified.calls, [])
})

test('latest server state blocks replacing an existing password and mismatched account', async () => {
  const { auth, calls } = mockAuth()
  await assert.rejects(registerPassword(auth, user.id, 'test-pass-123', 'test-pass-123', async () => ({ ...profile, password_registered: true })), { code: 'password_unavailable' })
  await assert.rejects(registerPassword(auth, user.id, 'test-pass-123', 'test-pass-123', async () => ({ ...profile, id: 'account-b' })), { code: 'session_changed' })
  assert.deepEqual(calls, [])
})

test('provider reauthentication and password errors do not appear as success or leak raw details', async () => {
  const { auth } = mockAuth({ updateUser: async () => ({ data: { user: null }, error: { code: 'reauthentication_needed', message: 'private-provider-data' } }) })
  try {
    await registerPassword(auth, user.id, 'test-pass-123', 'test-pass-123', async () => profile)
    assert.fail('Expected provider rejection')
  } catch (error) {
    assert.equal(error.code, 'reauthentication_needed')
    assert.match(accountUpdateMessage(error), /다시 로그인/)
    assert.doesNotMatch(String(error), /private-provider-data|test-pass-123/)
  }
})
