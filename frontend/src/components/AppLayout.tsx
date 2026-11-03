import { useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'

const navigation = [
  { to: '/', label: '홈', end: true },
  { to: '/new', label: '새 분석' },
]

export function AppLayout() {
  const { pathname } = useLocation()
  const historyActive = pathname === '/history' || pathname.startsWith('/history/')
  const [historyExpanded, setHistoryExpanded] = useState<boolean | null>(null)
  const expanded = historyExpanded ?? historyActive
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <NavLink className="brand" to="/">
          <span className="brand-mark">B</span>
          <span>
            <strong>Baseline AI</strong>
            <small>Basketball Shot Lab</small>
          </span>
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

      </aside>

      <main className="content-area">
        <Outlet />
      </main>
    </div>
  )
}
