import { Link } from 'react-router-dom'
import { useState } from 'react'
import { AnalysisHistory } from '../components/AnalysisHistory'
import type { RecordItem } from '../components/AnalysisHistory'
import { HomeAnalysisPreview } from '../components/HomeAnalysisPreview'
import './HomePage.css'

export function HomePage() {
  const [records, setRecords] = useState<RecordItem[] | null>(null)
  const latestResult = records?.find(record => !record.is_demo && record.video_available &&
    (record.status === 'completed' || record.status === 'review_required')) ?? null
  return (
    <div className="page home-page">
      <header className="home-hero">
        <div className="home-intro">
          <h1>농구 영상을<br />나만의 <em>슛 기록</em>으로</h1>
          <p className="home-description">슛 위치와 성공·실패를 분석하고,<br />3D 슈팅 맵과 히트맵으로 연습을 돌아보세요.</p>
          <div className="actions">
            <Link className="button primary home-start-button" to="/new">영상 분석 시작 <span aria-hidden="true">↗</span></Link>
            <Link className="button secondary" to="/history">분석 기록 보기</Link>
          </div>
          <div className="home-feature-tags"><span>슛 위치</span><span>성공·실패</span><span>장면 다시 보기</span></div>
        </div>
        <HomeAnalysisPreview analysis={latestResult} loading={records === null} />
      </header>

      <section className="home-how-it-works" aria-label="분석 이용 방법">
        <div className="home-flow-heading"><span className="home-section-kicker">HOW IT WORKS</span><h2>영상 하나로 시작하세요</h2></div>
        <ol className="home-flow">
          <li><span className="home-step-number">01</span><div><strong>영상 등록</strong><p>촬영한 농구 영상을 올리세요.</p></div></li>
          <li><span className="home-step-number">02</span><div><strong>코트 설정</strong><p>페인트존과 골대 위치를 맞추세요.</p></div></li>
          <li><span className="home-step-number">03</span><div><strong>결과 확인</strong><p>슛 위치와 장면을 다시 살펴보세요.</p></div></li>
        </ol>
      </section>
      <AnalysisHistory recent onRecordsChange={setRecords} />
    </div>
  )
}
