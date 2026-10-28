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
          <strong>Phase 1</strong>
          <p>프로젝트 실행 구조와 화면 이동을 준비하는 중입니다.</p>
        </div>
      </aside>

      <main className="content-area">
        <Outlet />
      </main>
    </div>
  )
}
