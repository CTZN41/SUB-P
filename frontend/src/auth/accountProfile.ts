import type { SupabaseClient } from '@supabase/supabase-js'

export type AccountProfile = {
  id: string
  nickname: string
  nickname_suggestion?: string
  nickname_conflict?: boolean
  email: string | null
  pending_email?: string | null
  can_register_email?: boolean
  email_confirmed: boolean
  email_reports_consent?: boolean
  providers: string[]
  created_at: string
  password_registered: boolean
  can_register_password: boolean
}

export function normalizeNickname(value: unknown): string {
  if (typeof value !== 'string') return ''
  const nickname = value.normalize('NFKC').trim().replace(/ +/g, ' ')
  return [...nickname].length >= 2 && [...nickname].length <= 20 && /^[\p{L}\p{M}\p{N}_ ]+$/u.test(nickname) ? nickname : ''
}

export const NICKNAME_HELP = '한글·영문·숫자·공백·밑줄을 사용해 2~20자로 입력해 주세요.'

export function accountNickname(metadata: unknown): string {
  return metadata && typeof metadata === 'object' ? normalizeNickname((metadata as Record<string, unknown>).shotlog_nickname) : ''
}

export function profileNickname(profile: AccountProfile | null, userId: string): string {
  return profile?.id === userId ? normalizeNickname(profile.nickname) : ''
}

export async function fetchAccountProfile(signal?: AbortSignal): Promise<AccountProfile> {
  const response = await fetch('/api/auth/profile', { credentials: 'same-origin', cache: 'no-store', signal })
  if (!response.ok) throw new Error(response.status === 401 ? '로그인이 만료됐습니다. 다시 로그인해 주세요.' : '프로필을 불러오지 못했습니다. 다시 시도해 주세요.')
  return response.json() as Promise<AccountProfile>
}

type AccountAuth = Pick<SupabaseClient['auth'], 'getUser' | 'updateUser'>

export class AccountUpdateError extends Error {
  code: string
  constructor(code: string) { super('계정 정보를 저장하지 못했습니다.'); this.name = 'AccountUpdateError'; this.code = code }
}

async function profilePost<T>(path: string, body: object, expectedId?: string): Promise<T> {
  const response = await fetch(`/api/auth/${path}`, { method: 'POST', credentials: 'same-origin', cache: 'no-store',
    headers: { 'Content-Type': 'application/json', ...(expectedId ? { 'X-Account-Id': expectedId } : {}) }, body: JSON.stringify(body) })
  if (!response.ok) {
    const failure = await response.json().catch(() => null)
    throw new AccountUpdateError(typeof failure?.detail?.code === 'string' ? failure.detail.code.toLowerCase() : 'update_failed')
  }
  return response.json() as Promise<T>
}

export async function checkSignup(nickname: string, email: string) {
  return profilePost<{ available: boolean }>('signup-check', { nickname: normalizeNickname(nickname), email: email.trim().toLowerCase() })
}

export async function claimNickname(value: string, expectedId: string): Promise<AccountProfile> {
  return profilePost<AccountProfile>('profile/nickname', { nickname: value }, expectedId)
}

export async function saveEmailReportConsent(expectedId: string, enabled: boolean): Promise<AccountProfile> {
  const profile = await profilePost<AccountProfile>('profile/email-preference', { enabled }, expectedId)
  if (profile.id !== expectedId) throw new AccountUpdateError('session_changed')
  if (profile.email_reports_consent !== enabled) throw new AccountUpdateError('email_consent_unconfirmed')
  return profile
}

export async function claimSuggestedNickname(profile: AccountProfile,
  claim: (value: string) => Promise<AccountProfile> = value => claimNickname(value, profile.id),
  load: () => Promise<AccountProfile> = fetchAccountProfile) {
  if (profile.nickname || profile.nickname_conflict || !profile.nickname_suggestion) return profile
  try {
    const saved = await claim(profile.nickname_suggestion)
    if (saved.id !== profile.id) throw new AccountUpdateError('session_changed')
    return saved
  } catch (error) {
    if (!(error instanceof AccountUpdateError) || !['nickname_taken', 'nickname_exists'].includes(error.code)) throw error
    const saved = await load()
    if (saved.id !== profile.id) throw new AccountUpdateError('session_changed')
    return saved
  }
}

async function verifyCurrentAccount(auth: AccountAuth, expectedId: string) {
  const result = await auth.getUser()
  if (result.error || !result.data.user || result.data.user.id !== expectedId) throw new AccountUpdateError('session_changed')
  return result.data.user
}

export async function registerNickname(auth: AccountAuth, expectedId: string, value: string,
  loadProfile: () => Promise<AccountProfile> = fetchAccountProfile,
  claim: (value: string) => Promise<AccountProfile> = value => claimNickname(value, expectedId)) {
  const nickname = normalizeNickname(value)
  if (!nickname) throw new AccountUpdateError('nickname_invalid')
  await verifyCurrentAccount(auth, expectedId)
  const result = await claim(nickname)
  if (result.id !== expectedId) throw new AccountUpdateError('session_changed')
  const saved = await loadProfile()
  if (saved.id !== expectedId) throw new AccountUpdateError('session_changed')
  if (saved.nickname !== result.nickname || !saved.nickname) throw new AccountUpdateError('nickname_unconfirmed')
  return saved
}

export async function registerEmail(auth: AccountAuth, expectedId: string, value: string, origin: string,
  loadProfile: () => Promise<AccountProfile> = fetchAccountProfile,
  check: (email: string) => Promise<unknown> = email => profilePost('profile/email-check', { email })) {
  const email = value.trim().toLowerCase()
  if (email.length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) throw new AccountUpdateError('email_invalid')
  const user = await verifyCurrentAccount(auth, expectedId)
  const profile = await loadProfile()
  if (profile.id !== expectedId) throw new AccountUpdateError('session_changed')
  if (user.email || profile.email || !profile.can_register_email) throw new AccountUpdateError('email_exists')
  await check(email)
  // A successful request is not proof of email ownership. Supabase sends and
  // validates the confirmation; the profile remains pending until reloaded.
  const result = await auth.updateUser({ email }, { emailRedirectTo: `${origin}/auth/callback` })
  if (result.error) throw new AccountUpdateError(result.error.code ?? 'update_failed')
  if (!result.data.user || result.data.user.id !== expectedId) throw new AccountUpdateError('session_changed')
  return result.data.user
}

export async function registerPassword(auth: AccountAuth, expectedId: string, password: string, confirmation: string,
  loadProfile: () => Promise<AccountProfile> = fetchAccountProfile) {
  if (password.length < 8 || password.length > 128) throw new AccountUpdateError('password_length')
  if (password !== confirmation) throw new AccountUpdateError('password_mismatch')
  const user = await verifyCurrentAccount(auth, expectedId)
  if (!user.email || !user.email_confirmed_at) throw new AccountUpdateError('email_not_confirmed')
  // Check the latest server state, rather than allowing a stale form to replace
  // an existing password. The actual update always uses the signed-in SDK user.
  const profile = await loadProfile()
  if (profile.id !== expectedId) throw new AccountUpdateError('session_changed')
  if (profile.password_registered || !profile.can_register_password) throw new AccountUpdateError('password_unavailable')
  const result = await auth.updateUser({ password })
  if (result.error) throw new AccountUpdateError(result.error.code ?? 'update_failed')
  if (!result.data.user || result.data.user.id !== expectedId) throw new AccountUpdateError('session_changed')
  return result.data.user
}

export function accountUpdateMessage(error: unknown): string {
  const code = error instanceof AccountUpdateError ? error.code : ''
  const messages: Record<string, string> = {
    nickname_invalid: NICKNAME_HELP,
    nickname_exists: '닉네임이 이미 등록됐습니다. 화면을 새로고침해 주세요.',
    nickname_taken: '이미 사용 중인 닉네임입니다. 다른 닉네임을 입력해 주세요.',
    nickname_unconfirmed: '닉네임 저장을 확인하지 못했습니다. 새로고침 후 내 프로필을 확인해 주세요.',
    password_length: '비밀번호는 8~128자로 입력해 주세요.',
    password_mismatch: '비밀번호가 서로 다릅니다.',
    password_unavailable: '비밀번호가 이미 등록됐거나 현재 계정에는 등록할 수 없습니다. 화면을 새로고침해 주세요.',
    email_not_confirmed: '인증된 이메일이 있어야 비밀번호를 등록할 수 있습니다.',
    email_verification_required: '이메일을 등록하고 인증한 후 동의해 주세요.',
    email_consent_unconfirmed: '수신 동의 저장을 확인하지 못했습니다. 내 프로필을 다시 확인해 주세요.',
    email_preference_busy: '설정을 저장 중입니다. 잠시 후 다시 확인해 주세요.',
    email_invalid: '이메일 주소를 확인해 주세요.',
    email_address_invalid: '이메일 주소를 확인해 주세요.',
    email_taken: '이미 사용 중인 이메일입니다. 다른 이메일을 입력해 주세요.',
    email_exists: '이미 등록됐거나 다른 계정에서 사용 중인 이메일입니다.',
    user_already_exists: '이미 사용 중인 이메일입니다. 기존 방법으로 로그인해 주세요.',
    over_email_send_rate_limit: '인증 메일 발송 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.',
    session_changed: '로그인 계정이 바뀌었거나 만료됐습니다. 다시 로그인해 주세요.',
    weak_password: '더 안전한 비밀번호를 입력해 주세요.',
    reauthentication_needed: '로그아웃 후 간편로그인으로 다시 로그인하고 비밀번호를 등록해 주세요.',
    reauthentication_not_valid: '로그아웃 후 간편로그인으로 다시 로그인해 주세요.',
    over_request_rate_limit: '요청이 많습니다. 잠시 후 다시 시도해 주세요.',
    user_sso_managed: '현재 계정에는 비밀번호를 등록할 수 없습니다.',
  }
  return messages[code] ?? '저장하지 못했습니다. 잠시 후 다시 시도해 주세요.'
}

export function profileProviderLabels(profile: AccountProfile): string[] {
  const labels: Record<string, string> = { google: 'Google', kakao: '카카오', 'custom:naver': '네이버', email: '이메일' }
  const methods = profile.providers.filter(provider => provider !== 'email').map(provider => labels[provider] ?? '간편로그인')
  if (profile.password_registered) methods.push('이메일·비밀번호')
  return [...new Set(methods)]
}
