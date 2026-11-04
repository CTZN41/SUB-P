import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { createClient } from '@supabase/supabase-js'
import type { Session, SupabaseClient } from '@supabase/supabase-js'
import { Navigate, useLocation } from 'react-router-dom'
import { safeAuthNext } from './authPolicy'
import { AuthContext, useAuth } from './authContext'
import type { AuthConfig as Config } from './authContext'
import { claimSuggestedNickname, fetchAccountProfile, profileNickname } from './accountProfile'
import type { AccountProfile } from './accountProfile'
import { createAuthStorage } from './sessionStorage'

let sessionQueue: Promise<void> = Promise.resolve()
function syncServerSession(token: string | null, remember = false) {
  const next = sessionQueue.catch(() => {}).then(async () => {
    const response = await fetch('/api/auth/session', { method: token ? 'POST' : 'DELETE', credentials: 'same-origin',
      headers: token ? { 'Content-Type': 'application/json' } : undefined, body: token ? JSON.stringify({ access_token: token, remember }) : undefined })
    if (!response.ok) throw new Error('로그인을 확인하지 못했습니다.')
  })
  sessionQueue = next
  return next
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<Config | null>(null)
  const [client, setClient] = useState<SupabaseClient | null>(null)
  const [session, setSession] = useState<Session | null>(null)
  const [profile, setProfile] = useState<AccountProfile | null>(null)
  const [remember, setRememberState] = useState(false)
  const storage = useRef<ReturnType<typeof createAuthStorage> | null>(null)
  const currentUser = useRef<string | null>(null)
  useEffect(() => { currentUser.current = session?.user.id ?? null }, [session?.user.id])
  const [loading, setLoading] = useState(true)
  const [readyToken, setReadyToken] = useState<string | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    let unsubscribe: (() => void) | undefined
    let auth: SupabaseClient | null = null
    fetch('/api/auth/config').then(async response => {
      // The running pre-auth local backend may not yet have this new route.
      if (response.status === 404 && import.meta.env.DEV) return { required: false, configured: false, url: null, publishableKey: null, providers: { google: false, naver: false, kakao: false } } as Config
      if (!response.ok) throw new Error('로그인 연결 상태를 확인하지 못했습니다.')
      return response.json() as Promise<Config>
    }).then(async data => {
      if (!active) return
      setConfig(data)
      if (data.configured && data.url && data.publishableKey) {
        const storageKey = `sb-${new URL(data.url).hostname.split('.')[0]}-auth-token`
        const adapter = createAuthStorage(window.localStorage, window.sessionStorage, storageKey)
        storage.current = adapter
        setRememberState(adapter.remember())
        auth = createClient(data.url, data.publishableKey, { auth: { flowType: 'pkce', detectSessionInUrl: false,
          persistSession: true, storageKey, storage: adapter.storage } })
        setClient(auth)
        const { data: listener } = auth.auth.onAuthStateChange((_event, current) => { if (active) setSession(current) })
        unsubscribe = () => listener.subscription.unsubscribe()
        const { data: initial, error: initialError } = await auth.auth.getSession()
        if (initialError) throw initialError
        if (active) setSession(initial.session)
      }
    }).catch(() => { if (active) setError('로그인 연결 상태를 확인하지 못했습니다. 새로고침 후 다시 시도해 주세요.') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false; unsubscribe?.(); auth?.auth.stopAutoRefresh() }
  }, [])
  const token = session?.access_token ?? null
  const userId = session?.user.id ?? null
  useEffect(() => {
    if (!config?.configured) return
    let active = true
    const controller = new AbortController()
    syncServerSession(token, remember).then(async () => {
      if (!active) return
      const fetched = userId ? await fetchAccountProfile(controller.signal) : null
      if (!active) return
      if (fetched && fetched.id !== userId) throw new Error('Account changed')
      const value = fetched ? await claimSuggestedNickname(fetched) : null
      if (!active) return
      if (value && value.id !== userId) throw new Error('Account changed')
      setProfile(value); setReadyToken(token); setError('')
    }).catch(() => { if (active) { setReadyToken(null); setError('로그인과 프로필을 확인하지 못했습니다. 새로고침 후 다시 시도해 주세요.') } })
    return () => { active = false; controller.abort() }
  }, [token, userId, remember, config?.configured])
  function setRemember(value: boolean) {
    if (!storage.current) throw new Error('로그인 연결을 준비하고 있습니다.')
    storage.current.setRemember(value)
    setRememberState(value)
  }
  async function refreshProfile() {
    const expected = currentUser.current
    if (!expected) throw new Error('로그인이 필요합니다.')
    const value = await fetchAccountProfile()
    if (value.id !== expected || currentUser.current !== expected) throw new Error('로그인 계정이 바뀌었습니다.')
    setProfile(value)
  }
  async function signOut() {
    await syncServerSession(null)
    const result = await client?.auth.signOut({ scope: 'local' })
    if (result?.error) {
      setError('로그아웃을 완료하지 못했습니다. 다시 로그인하거나 잠시 후 시도해 주세요.')
      setReadyToken(null)
      throw result.error
    }
    setReadyToken(null)
    setProfile(null)
    setSession(null)
  }
  const ownProfile = profile?.id === userId ? profile : null
  return <AuthContext.Provider value={{ config, client, session, profile: ownProfile, loading,
    ready: !!token && readyToken === token && !!ownProfile, error, remember, setRemember, refreshProfile, signOut }}>{children}</AuthContext.Provider>
}

export function AuthGate({ children }: { children: ReactNode }) {
  const auth = useAuth()
  const location = useLocation()
  if (auth.loading) return <div className="page"><p role="status">화면을 준비하고 있습니다…</p></div>
  if (!auth.config || (auth.config.required && auth.error)) return <div className="page"><p className="form-error" role="alert">{auth.error || '로그인 연결을 확인해 주세요.'}</p><a className="button secondary" href="/login">로그인 화면</a></div>
  if (!auth.config.required) return <>{children}</>
  if (!auth.session) return <Navigate to={`/login?next=${encodeURIComponent(safeAuthNext(location.pathname + location.search))}`} replace />
  if (!auth.ready) return <div className="page"><p role="status">로그인을 확인하고 있습니다…</p></div>
  if (!profileNickname(auth.profile, auth.session.user.id) && location.pathname !== '/profile') {
    return <Navigate to={`/profile?setup=nickname&next=${encodeURIComponent(safeAuthNext(location.pathname + location.search))}`} replace />
  }
  return <div key={auth.session.user.id}>{children}</div>
}
