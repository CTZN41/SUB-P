import { AnalysisHistory } from '../components/AnalysisHistory'
import { NavLink } from 'react-router-dom'

export function HistoryPage() {
  return <div className="page history-page"><header className="setup-header"><div><p className="eyebrow">SAVED ANALYSES</p><h1>분석 기록</h1></div></header><nav className="history-view-tabs"><NavLink to="/history" end>영상별</NavLink><NavLink to="/history/monthly">월별</NavLink></nav><AnalysisHistory /></div>
}
