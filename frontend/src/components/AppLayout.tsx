import { useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from '../auth/authContext'
import { profileNickname } from '../auth/accountProfile'

const navigation = [
  { to: '/', label: '홈', end: true },
  { to: '/new', label: '새 분석' },
]

export function AppLayout() {
  const auth = useAuth()
  const [authError, setAuthError] = useState('')
  const { pathname } = useLocation()
  const historyActive = pathname === '/history' || pathname.startsWith('/history/')
  const [historyExpanded, setHistoryExpanded] = useState<boolean | null>(null)
  const expanded = historyExpanded ?? historyActive
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <NavLink className="brand" to="/">
          <strong>ShotLog</strong>
        </NavLink>

        <nav className="main-nav" aria-label="주요 메뉴">
          {navigation.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
            >
              {item.label}
            </NavLink>
          ))}
          <button type="button" className={`nav-link history-menu-toggle${historyActive ? ' active' : ''}`} aria-expanded={expanded} aria-controls="history-submenu" onClick={() => setHistoryExpanded(!expanded)}>
            <span>분석 기록</span><span aria-hidden="true">{expanded ? '▾' : '▸'}</span>
          </button>
          {expanded && <div id="history-submenu" className="history-submenu">
            <NavLink to="/history" end className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>영상별</NavLink>
            <NavLink to="/history/monthly" className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>월별</NavLink>
          </div>}
        </nav>
        <div className="sidebar-account">{auth.session ? <><span>{profileNickname(auth.profile, auth.session.user.id) || '내 계정'}</span><NavLink className="button secondary" to="/profile">내 프로필</NavLink><button className="button secondary" onClick={() => { setAuthError(''); void auth.signOut().catch(() => setAuthError('로그아웃하지 못했습니다. 다시 시도해 주세요.')) }}>로그아웃</button></> : <NavLink className="button secondary" to="/login">로그인</NavLink>}{authError && <span role="alert">{authError}</span>}</div>
      </aside>

      <main className="content-area">
        <Outlet />
      </main>
    </div>
  )
}
