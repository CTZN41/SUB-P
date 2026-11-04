import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useAuth } from '../auth/authContext'
import { safeAuthNext } from '../auth/authPolicy'
import { accountUpdateMessage, fetchAccountProfile, NICKNAME_HELP, normalizeNickname,
  profileProviderLabels, registerEmail, registerNickname, registerPassword } from '../auth/accountProfile'
import type { AccountProfile } from '../auth/accountProfile'
import './ProfilePage.css'

export function ProfilePage() {
  const auth = useAuth()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const setup = params.get('setup') === 'nickname'
  const next = safeAuthNext(params.get('next'))
  const userId = auth.session?.user.id
  const [profile, setProfile] = useState<AccountProfile | null>(null)
  const [loadError, setLoadError] = useState('')
  const [reloadCount, setReloadCount] = useState(0)
  const [nickname, setNickname] = useState('')
  const [email, setEmail] = useState('')
  const [emailRequested, setEmailRequested] = useState(false)
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [busy, setBusy] = useState<'nickname' | 'password' | 'email' | 'refresh' | null>(null)
  const [updateError, setUpdateError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    if (!auth.ready || !userId) return
    const controller = new AbortController()
    fetchAccountProfile(controller.signal).then(value => {
      if (controller.signal.aborted) return
      if (value.id !== userId) throw new Error('로그인 계정을 다시 확인해 주세요.')
      setProfile(value); setLoadError('')
    }).catch(error => {
      if (!controller.signal.aborted) setLoadError(error instanceof Error ? error.message : '프로필을 불러오지 못했습니다.')
    })
    return () => controller.abort()
  }, [auth.ready, userId, reloadCount])

  useEffect(() => {
    if (setup && profile?.nickname && auth.profile && auth.profile.id === userId && auth.profile.nickname) {
      navigate(next === '/profile' || next.startsWith('/profile?') ? '/' : next, { replace: true })
    }
  }, [setup, profile?.nickname, auth.profile, userId, navigate, next])

  async function saveNickname(event: FormEvent) {
    event.preventDefault()
    if (!auth.client || !profile || !userId || busy) return
    setBusy('nickname'); setUpdateError(''); setNotice('')
    try {
      const saved = await registerNickname(auth.client.auth, userId, nickname)
      setProfile(saved)
      await auth.refreshProfile()
      setNotice('닉네임을 등록했습니다.')
    } catch (error) { setUpdateError(accountUpdateMessage(error)) }
    finally { setBusy(null) }
  }

  async function saveEmail(event: FormEvent) {
    event.preventDefault()
    if (!auth.client || !profile || !userId || busy) return
    setBusy('email'); setUpdateError(''); setNotice('')
    try {
      sessionStorage.setItem('shotlog_auth_next', '/profile')
      await registerEmail(auth.client.auth, userId, email, window.location.origin)
      setEmailRequested(true)
      setReloadCount(value => value + 1)
      setNotice('인증 메일을 요청했습니다. 받은 링크에서 이메일 등록을 완료해 주세요.')
    } catch (error) { setUpdateError(accountUpdateMessage(error)) }
    finally { setBusy(null) }
  }

  async function refreshEmail() {
    if (!userId || busy) return
    setBusy('refresh'); setUpdateError(''); setNotice('')
    try {
      const saved = await fetchAccountProfile()
      if (saved.id !== userId) throw new Error('Account changed')
      setProfile(saved)
      await auth.refreshProfile()
      setNotice(saved.email && saved.email_confirmed ? '이메일 인증이 완료됐습니다.' : '이메일 인증을 기다리고 있습니다. 받은 메일의 링크를 열어 주세요.')
    } catch (error) { setUpdateError(accountUpdateMessage(error)) }
    finally { setBusy(null) }
  }

  async function savePassword(event: FormEvent) {
    event.preventDefault()
    if (!auth.client || !profile || !userId || busy) return
    setBusy('password'); setUpdateError(''); setNotice('')
    try {
      await registerPassword(auth.client.auth, userId, password, confirmation)
      setProfile({ ...profile, password_registered: true, can_register_password: false })
      setPassword(''); setConfirmation(''); setShowPassword(false)
      setNotice('비밀번호를 등록했습니다. 이제 같은 이메일과 등록한 비밀번호로 로그인할 수 있습니다.')
    } catch (error) { setUpdateError(accountUpdateMessage(error)) }
    finally { setBusy(null) }
  }

  if (!auth.session) return <div className="page profile-page"><h1>내 프로필</h1><Link className="button primary" to="/login?next=%2Fprofile">로그인</Link></div>
  const date = profile ? new Date(profile.created_at) : null
  return <div className="page profile-page" aria-busy={!!busy}>
    <header className="profile-header"><p className="eyebrow">MY PROFILE</p><h1>{setup ? '닉네임 설정' : '내 프로필'}</h1></header>
    {loadError ? <section className="profile-card"><p className="form-error" role="alert">{loadError}</p><button className="button secondary" onClick={() => setReloadCount(value => value + 1)}>다시 확인</button></section>
      : !profile ? <p role="status">프로필을 불러오고 있습니다…</p>
      : <>
        {!profile.nickname && <section className="profile-card profile-nickname" aria-labelledby="nickname-heading">
          <h2 id="nickname-heading">사용할 닉네임을 정해 주세요</h2>
          {profile.nickname_conflict && <p className="profile-help">기존 닉네임을 먼저 가입한 계정이 사용하고 있습니다. 새 닉네임을 설정해 주세요.</p>}
          <form className="auth-form profile-form" onSubmit={saveNickname}>
            <label>닉네임<input name="nickname" autoComplete="nickname" required minLength={2} maxLength={20} value={nickname} onChange={event => setNickname(event.target.value)} disabled={!!busy} aria-describedby="profile-nickname-help" /></label>
            <p className="profile-help" id="profile-nickname-help">{NICKNAME_HELP}</p>
            <button className="button primary" disabled={!!busy || !normalizeNickname(nickname)}>{busy === 'nickname' ? '저장 중…' : setup ? '저장하고 계속' : '닉네임 등록'}</button>
          </form>
        </section>}
        <div className="profile-columns">
          <section className="profile-card" aria-labelledby="account-heading">
            <h2 id="account-heading">계정 정보</h2>
            <dl className="profile-details">
              <div><dt>닉네임</dt><dd>{profile.nickname || '미설정'}</dd></div>
              <div><dt>이메일</dt><dd>{profile.email || '등록된 이메일 없음'}{profile.email && !profile.email_confirmed ? ' · 인증 대기' : ''}</dd></div>
              <div><dt>로그인 방법</dt><dd>{profileProviderLabels(profile).join(' · ') || '확인 필요'}</dd></div>
              <div><dt>가입일</dt><dd>{date && Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat('ko-KR', { dateStyle: 'medium', timeZone: 'Asia/Seoul' }).format(date) : '확인 필요'}</dd></div>
            </dl>
            {profile.can_register_email && <form className="auth-form profile-form profile-email-form" onSubmit={saveEmail}>
              <h3>이메일 등록</h3>
              {profile.pending_email && <p className="profile-help">{profile.pending_email} · 인증 대기</p>}
              <label>이메일<input name="email" type="email" autoComplete="email" required maxLength={254} value={email} onChange={event => { setEmail(event.target.value); setEmailRequested(false) }} disabled={!!busy} /></label>
              <button className="button primary" disabled={!!busy || !email.trim() || emailRequested}>{busy === 'email' ? '요청 중…' : '인증 메일 보내기'}</button>
            </form>}
            {(profile.pending_email || emailRequested || (profile.email && !profile.email_confirmed)) && <button type="button" className="button secondary profile-email-refresh" disabled={!!busy} onClick={() => void refreshEmail()}>등록 상태 확인</button>}
          </section>
          <section className="profile-card" aria-labelledby="password-heading">
            <h2 id="password-heading">이메일 로그인 비밀번호</h2>
            {profile.password_registered ? <p className="profile-password-status">등록됨</p>
              : !profile.can_register_password ? <p className="profile-help">인증된 이메일이 있는 간편로그인 계정에서 등록할 수 있습니다.</p>
              : <form className="auth-form profile-form" onSubmit={savePassword}>
                <p className="profile-help">비밀번호를 등록하면 간편로그인과 같은 이메일로 로그인할 수 있습니다.</p>
                <label>비밀번호<span className="auth-password"><input name="new_password" type={showPassword ? 'text' : 'password'} autoComplete="new-password" required minLength={8} maxLength={128} placeholder="8자 이상 입력해 주세요" value={password} onChange={event => setPassword(event.target.value)} disabled={!!busy} /><button type="button" disabled={!!busy} aria-label={showPassword ? '비밀번호 숨기기' : '비밀번호 보기'} aria-pressed={showPassword} onClick={() => setShowPassword(value => !value)}>{showPassword ? '숨기기' : '보기'}</button></span></label>
                <label>비밀번호 확인<input name="password_confirmation" type={showPassword ? 'text' : 'password'} autoComplete="new-password" required minLength={8} maxLength={128} value={confirmation} onChange={event => setConfirmation(event.target.value)} disabled={!!busy} /></label>
                <button className="button primary" disabled={!!busy || password.length < 8 || password !== confirmation}>{busy === 'password' ? '등록 중…' : '비밀번호 등록'}</button>
              </form>}
          </section>
        </div>
        {updateError && <p className="form-error profile-feedback" role="alert">{updateError}</p>}
        {notice && <p className="success-note profile-feedback" role="status">{notice}</p>}
      </>}
  </div>
}
