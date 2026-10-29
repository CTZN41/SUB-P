import { NavLink, Outlet } from 'react-router-dom'

const navigation = [
  { to: '/', label: '홈', end: true },
  { to: '/new', label: '새 분석' },
  { to: '/history', label: '분석 기록' },
]

export function AppLayout() {
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
        </nav>

        <div className="sidebar-note">
          <span>현재 단계</span>
          <strong>Phase 9</strong>
          <p>원본 장면을 보며 자동 슛 기록을 확인·수정할 수 있습니다.</p>
        </div>
      </aside>

      <main className="content-area">
        <Outlet />
      </main>
    </div>
  )
}
