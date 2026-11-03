import { Link } from 'react-router-dom'
import { AnalysisHistory } from '../components/AnalysisHistory'

export function HomePage() {
  return (
    <div className="page home-page">
      <header className="page-header">
        <div className="home-start-actions">
          <h1>ShotLog</h1>
          <div className="actions">
            <Link className="button primary" to="/new">새 영상 분석 준비</Link>
            <Link className="button secondary" to="/history">분석 기록 보기</Link>
          </div>
        </div>
      </header>

      <AnalysisHistory recent />
    </div>
  )
}
