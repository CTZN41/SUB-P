import { AnalysisHistory } from '../components/AnalysisHistory'
import { NavLink } from 'react-router-dom'

export function HistoryPage() {
  return <div className="page history-page"><header className="setup-header"><div><p className="eyebrow">SAVED ANALYSES</p><h1>분석 기록</h1><p className="lead">저장된 영상과 슛 결과를 다시 열거나 함께 삭제할 수 있습니다.</p></div></header><nav className="history-view-tabs"><NavLink to="/history" end>일별 기록</NavLink><NavLink to="/history/monthly">월별 결과</NavLink></nav><AnalysisHistory /></div>
}
