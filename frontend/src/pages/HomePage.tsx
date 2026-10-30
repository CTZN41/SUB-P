import { Link } from 'react-router-dom'
import { AnalysisHistory } from '../components/AnalysisHistory'

const statusItems = [
  { label: 'React 화면', state: '준비 완료' },
  { label: 'FastAPI 로컬 API', state: '준비 완료' },
  { label: 'MP4 영상 등록', state: '사용 가능' },
]

export function HomePage() {
  return (
    <div className="page home-page">
      <header className="page-header">
        <div>
          <p className="eyebrow">BASKETBALL VIDEO ANALYSIS</p>
          <h1>내 슛을 기록으로 바꾸는<br />개인 농구 분석 도구</h1>
          <p className="lead">
            하프코트 영상을 올리면 슈팅 위치와 성공 여부를 분석하고,
            O/X 슈팅 맵과 구역별 히트맵으로 확인할 수 있도록 만들 예정입니다.
          </p>
          <div className="actions">
            <Link className="button primary" to="/new">새 영상 분석 준비</Link>
            <Link className="button secondary" to="/history">분석 기록 보기</Link>
          </div>
        </div>
        <div className="court-preview" aria-label="하프코트 미리보기">
          <div className="court-line three-point" />
          <div className="court-line key" />
          <div className="court-line rim" />
          <span className="shot-dot made">O</span>
          <span className="shot-dot missed">X</span>
        </div>
      </header>

      <section className="status-grid" aria-label="개발 준비 상태">
        {statusItems.map((item) => (
          <article className="status-card" key={item.label}>
            <span className="status-light" />
            <div>
              <p>{item.label}</p>
              <strong>{item.state}</strong>
            </div>
          </article>
        ))}
      </section>

      <section className="notice-card">
        <div>
          <p className="eyebrow">PHASE 3 안내</p>
          <h2>이제 MP4 영상을 안전하게 등록할 수 있습니다.</h2>
        </div>
        <p>
          영상 형식과 길이·용량, 촬영 동의와 기본 화질을 확인해 로컬 PC에 저장합니다.
          코트 기준점, 선수 추적, 슛 판정과 결과 지도는 다음 Phase부터 연결합니다.
        </p>
      </section>
      <AnalysisHistory recent />
    </div>
  )
}
