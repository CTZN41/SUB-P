import { NavLink } from 'react-router-dom'
import { MonthlyResults } from '../components/MonthlyResults'

export function MonthlyResultsPage() {
  return <div className="page shot-review-page">
    <header className="setup-header"><div><p className="eyebrow">MONTHLY RESULTS</p><h1>월별 분석 결과</h1><p className="lead">저장된 슛을 한 달 단위로 모아 확인합니다.</p></div></header>
    <nav className="history-view-tabs"><NavLink to="/history" end>일별 기록</NavLink><NavLink to="/history/monthly">월별 결과</NavLink></nav>
    <MonthlyResults />
  </div>
}
