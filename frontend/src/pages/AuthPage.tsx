import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useAuth } from '../auth/authContext'
import { authMessage, safeAuthNext } from '../auth/authPolicy'
import type { Provider } from '@supabase/supabase-js'
import './AuthPage.css'

export function AuthPage({ signup = false }: { signup?: boolean }) {
  const auth = useAuth()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const next = safeAuthNext(params.get('next'))
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const ready = !!auth.client && !!auth.config?.configured
  useEffect(() => { if (auth.ready) navigate(next, { replace: true }) }, [auth.ready, navigate, next])
  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!auth.client || busy) return
    setError(''); setNotice('')
    if (signup && password !== confirmation) { setError('비밀번호가 서로 다릅니다.'); return }
    setBusy(true)
    try {
      const response = signup
        ? await auth.client.auth.signUp({ email: email.trim(), password,
            options: { emailRedirectTo: `${window.location.origin}/auth/callback` } })
        : await auth.client.auth.signInWithPassword({ email: email.trim(), password })
      if (response.error) { setError(authMessage(response.error.code)); return }
      if (signup && !response.data.session) {
        setNotice('인증 메일을 보냈습니다. 받은 메일에서 가입을 완료해 주세요.')
        setPassword(''); setConfirmation('')
      }
    } catch { setError('연결하지 못했습니다. 잠시 후 다시 시도해 주세요.') }
    finally { setBusy(false) }
  }
  async function social(provider: 'naver' | 'google' | 'kakao') {
    if (!auth.client || !auth.config?.providers[provider] || busy) return
    setBusy(true); setError('')
    try {
      sessionStorage.setItem('shotlog_auth_next', next)
      const { error: failure } = await auth.client.auth.signInWithOAuth({
        provider: (provider === 'naver' ? 'custom:naver' : provider) as Provider,
        options: { redirectTo: `${window.location.origin}/auth/callback` },
      })
      if (failure) { setError(authMessage(failure.code)); setBusy(false) }
    } catch { setError('간편 로그인에 연결하지 못했습니다.'); setBusy(false) }
  }
  return <main className="auth-page">
    <Link className="auth-wordmark" to="/">ShotLog</Link>
    <section className="auth-card" aria-labelledby="auth-title">
      <header className="auth-heading"><p className="eyebrow">YOUR NEXT SHOT</p><h1 id="auth-title">{signup ? '회원가입' : '로그인'}</h1></header>
      <nav className="auth-tabs" aria-label="계정 메뉴">
        <Link to={`/login?next=${encodeURIComponent(next)}`} aria-current={!signup ? 'page' : undefined}>로그인</Link>
        <Link to={`/signup?next=${encodeURIComponent(next)}`} aria-current={signup ? 'page' : undefined}>회원가입</Link>
      </nav>
      {!auth.loading && !ready && <p className="auth-notice" role="status">로그인 연결을 준비하고 있습니다.</p>}
      {auth.error && <p className="form-error" role="alert">{auth.error}</p>}
      <form className="auth-form" onSubmit={submit}>
        <label>이메일<input name="email" type="email" autoComplete="email" required maxLength={254} placeholder="you@example.com" value={email} onChange={e => setEmail(e.target.value)} disabled={busy} /></label>
        <label>비밀번호<span className="auth-password"><input name="password" type={showPassword ? 'text' : 'password'} autoComplete={signup ? 'new-password' : 'current-password'} required minLength={signup ? 8 : undefined} maxLength={128} placeholder={signup ? '8자 이상 입력해 주세요' : '비밀번호를 입력해 주세요'} value={password} onChange={e => setPassword(e.target.value)} disabled={busy} /><button type="button" aria-label={showPassword ? '비밀번호 숨기기' : '비밀번호 보기'} aria-pressed={showPassword} onClick={() => setShowPassword(value => !value)}>{showPassword ? '숨기기' : '보기'}</button></span></label>
        {signup && <label>비밀번호 확인<input name="password_confirmation" type={showPassword ? 'text' : 'password'} autoComplete="new-password" required minLength={8} maxLength={128} placeholder="비밀번호를 한 번 더 입력해 주세요" value={confirmation} onChange={e => setConfirmation(e.target.value)} disabled={busy} /></label>}
        {error && <p className="form-error" role="alert">{error}</p>}
        {notice && <p className="success-note" role="status">{notice}</p>}
        <button className="button primary auth-submit" type="submit" disabled={!ready || busy}>{busy ? '연결 중…' : signup ? '이메일로 가입하기' : '로그인'}</button>
      </form>
      <div className="auth-divider"><span>또는 간편 로그인</span></div>
      <div className="auth-social" aria-label="간편 로그인">
        <button className="social-button google" type="button" disabled={busy || !ready || !auth.config?.providers.google} onClick={() => social('google')}><span className="social-symbol" aria-hidden="true">G</span>Google로 계속하기</button>
        <button className="social-button naver" type="button" disabled={busy || !ready || !auth.config?.providers.naver} onClick={() => social('naver')}><span className="social-symbol" aria-hidden="true">N</span>네이버로 계속하기</button>
        <button className="social-button kakao" type="button" disabled={busy || !ready || !auth.config?.providers.kakao} onClick={() => social('kakao')}><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 3C6.5 3 2 6.4 2 10.6c0 2.7 1.9 5.1 4.8 6.5L6 21l4.4-2.9 1.6.1c5.5 0 10-3.4 10-7.6S17.5 3 12 3Z" fill="currentColor" /></svg>카카오로 계속하기</button>
      </div>
    </section>
    <Link className="auth-home" to="/">홈으로 돌아가기</Link>
  </main>
}

export function AuthCallbackPage() {
  const auth = useAuth()
  const navigate = useNavigate()
  const started = useRef(false)
  const [error, setError] = useState('')
  const [code] = useState(() => new URLSearchParams(window.location.search).get('code'))
  const next = safeAuthNext(sessionStorage.getItem('shotlog_auth_next'))
  useEffect(() => {
    if (auth.ready) { sessionStorage.removeItem('shotlog_auth_next'); navigate(next, { replace: true }) }
  }, [auth.ready, navigate, next])
  useEffect(() => {
    if (auth.loading || !auth.client || started.current) return
    started.current = true
    if (!code) return
    auth.client.auth.exchangeCodeForSession(code).then(({ error: failure }) => {
      window.history.replaceState(null, '', '/auth/callback')
      if (failure) setError(authMessage(failure.code))
    }).catch(() => setError('로그인 연결을 완료하지 못했습니다. 다시 시도해 주세요.'))
  }, [auth.client, auth.loading, code])
  return <main className="auth-page"><Link className="auth-wordmark" to="/">ShotLog</Link><section className="auth-card"><h1>로그인 확인</h1>
    <p role={error || (auth.client && !code) ? 'alert' : 'status'}>{error || auth.error || (!auth.loading && !auth.client ? '로그인 연결을 준비하고 있습니다.' : !code ? '로그인이 취소되었거나 연결이 만료됐습니다. 다시 로그인해 주세요.' : '잠시만 기다려 주세요…')}</p>
    {(error || auth.error || (!auth.loading && !auth.client) || !code) && <Link className="button primary" to="/login">로그인으로 돌아가기</Link>}
  </section></main>
}
