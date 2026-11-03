import { Link } from 'react-router-dom'

export function AnalysisBackLink({ to }: { to: string }) {
  return <nav className="analysis-back-nav" aria-label="이전 화면"><Link className="button secondary" to={to}>← 뒤로가기</Link></nav>
}
