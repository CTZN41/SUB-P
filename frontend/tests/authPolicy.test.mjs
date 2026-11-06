import test from 'node:test'
import assert from 'node:assert/strict'
import { safeAuthNext, authMessage, socialLoginOptions } from '../src/auth/authPolicy.ts'
import { createClient } from '@supabase/supabase-js'

test('login never redirects outside the application or back into an auth loop', () => {
  for (const path of ['https://evil.example', '//evil.example', '/\\evil.example', '/login', '/signup', '/auth/callback', '/history\n?x', null]) assert.equal(safeAuthNext(path), '/')
  for (const path of ['/', '/new', '/profile', '/profile?setup=nickname', '/history', '/history/monthly', '/results/test-id', '/progress/test-id']) assert.equal(safeAuthNext(path), path)
})

test('provider errors show safe Korean messages rather than raw credentials or server details', () => {
  assert.match(authMessage('invalid_credentials'), /이메일/)
  assert.match(authMessage('email_not_confirmed'), /인증/)
  assert.match(authMessage('email_address_not_authorized'), /보낼 수 없습니다/)
  assert.match(authMessage('over_email_send_rate_limit'), /한도/)
  assert.doesNotMatch(authMessage('private-provider-payload'), /private-provider-payload/)
})

test('Kakao alone replaces default email scope without changing redirect or other providers', async () => {
  const origin = 'http://127.0.0.1:5173'
  const kakao = socialLoginOptions('kakao', origin)
  assert.deepEqual(kakao, { redirectTo: `${origin}/auth/callback`, queryParams: { scope: 'profile_nickname' } })
  assert.deepEqual(socialLoginOptions('google', origin), { redirectTo: `${origin}/auth/callback` })
  const client = createClient('https://test-project.example', 'test-public-key', { auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false } })
  const { data, error } = await client.auth.signInWithOAuth({ provider: 'kakao', options: { ...kakao, skipBrowserRedirect: true } })
  assert.equal(error, null)
  const url = new URL(data.url)
  assert.equal(url.pathname, '/auth/v1/authorize')
  assert.equal(url.searchParams.get('provider'), 'kakao')
  assert.equal(url.searchParams.get('scope'), 'profile_nickname')
  assert.equal(url.searchParams.get('scopes'), null)
  assert.equal(url.searchParams.get('redirect_to'), `${origin}/auth/callback`)
})

test('retrying a cancelled OAuth attempt creates fresh PKCE challenges for the same or another provider', async () => {
  const values = new Map()
  const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) }
  const client = createClient('https://test-project.example', 'test-public-key', { auth: {
    storage, storageKey: 'cancel-retry-fixture', flowType: 'pkce', persistSession: true,
    autoRefreshToken: false, detectSessionInUrl: false,
  } })
  const challenges = []
  for (const provider of ['google', 'google', 'kakao']) {
    const { data, error } = await client.auth.signInWithOAuth({ provider,
      options: { ...socialLoginOptions(provider, 'http://127.0.0.1:5173'), skipBrowserRedirect: true } })
    assert.equal(error, null)
    const url = new URL(data.url)
    assert.equal(url.searchParams.get('redirect_to'), 'http://127.0.0.1:5173/auth/callback')
    assert.equal(url.searchParams.get('code_challenge_method'), 's256')
    assert.ok(values.get('cancel-retry-fixture-code-verifier'))
    challenges.push(url.searchParams.get('code_challenge'))
  }
  assert.equal(new Set(challenges).size, 3)
})
