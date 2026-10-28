import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

const API_BASE = 'http://127.0.0.1:8000/api'
const INITIAL_POINTS = [[12, 78], [88, 78], [72, 25], [28, 25]]
const COURT_POINTS = [[0, 0], [50, 0], [50, 47], [0, 47]]

type Analysis = { id: string; title: string; duration_ms: number; width: number; height: number; play_type: string }

export function CourtCalibrationPage() {
  const { analysisId = '' } = useParams()
  const navigate = useNavigate()
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [timeMs, setTimeMs] = useState(0)
  const [points, setPoints] = useState(INITIAL_POINTS)
  const [hoop, setHoop] = useState([50, 18])
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const frameUrl = useMemo(() => `${API_BASE}/analyses/${analysisId}/calibration/frame?time_ms=${timeMs}`, [analysisId, timeMs])

  useEffect(() => {
    fetch(`${API_BASE}/analyses/${analysisId}`).then(async (response) => {
      if (!response.ok) throw new Error('분석 기록을 불러오지 못했습니다.')
      setAnalysis(await response.json())
    }).catch((caught) => setError(caught.message))
  }, [analysisId])

  function movePoint(index: number, event: React.PointerEvent<HTMLDivElement>) {
    const rect = event.currentTarget.getBoundingClientRect()
    const next = [
      Math.max(0, Math.min(100, ((event.clientX - rect.left) / rect.width) * 100)),
      Math.max(0, Math.min(100, ((event.clientY - rect.top) / rect.height) * 100)),
    ]
    if (index === 4) setHoop(next)
    else setPoints((current) => current.map((point, pointIndex) => pointIndex === index ? next : point))
  }

  async function save() {
    if (!analysis) return
    setSaving(true); setError('')
    const toPixels = (point: number[]) => [point[0] * analysis.width / 100, point[1] * analysis.height / 100]
    try {
      const response = await fetch(`${API_BASE}/analyses/${analysisId}/calibration`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ frame_time_ms: timeMs, image_points: points.map(toPixels), court_points: COURT_POINTS, hoop_point: toPixels(hoop) }),
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(payload.detail?.message ?? '코트 위치를 저장하지 못했습니다.')
      navigate(`/players/${analysisId}`)
    } catch (caught) { setError(caught instanceof Error ? caught.message : '코트 위치를 저장하지 못했습니다.') }
    finally { setSaving(false) }
  }

  return <div className="page setup-page">
    <header className="setup-header"><div><p className="eyebrow">COURT SETUP</p><h1>코트 위치 확인</h1><p className="lead">초기 점은 안내용입니다. 실제 바닥 모서리 네 곳과 골대 중심에 맞게 직접 눌러 옮겨 주세요.</p></div><span className="step-chip">1 / 3 설정</span></header>
    <section className="setup-card">
      <div className="frame-toolbar"><strong>{analysis?.title ?? '영상을 불러오는 중'}</strong><label>대표 장면<input type="range" min="0" max={analysis?.duration_ms ?? 0} step="500" value={timeMs} onChange={(e) => setTimeMs(Number(e.target.value))} /></label><span>{(timeMs / 1000).toFixed(1)}초</span></div>
      <div className="calibration-frame" style={{ backgroundImage: `url(${frameUrl})` }} onPointerDown={(event) => {
        const target = event.target as HTMLElement
        const index = Number(target.dataset.point)
        if (!Number.isNaN(index)) movePoint(index, event)
      }}>
        <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><polygon points={points.map((point) => point.join(',')).join(' ')} /></svg>
        {points.map((point, index) => <button key={index} data-point={index} className="calibration-point" style={{ left: `${point[0]}%`, top: `${point[1]}%` }} aria-label={`바닥 기준점 ${index + 1}`}>{index + 1}</button>)}
        <button data-point="4" className="calibration-point hoop-point" style={{ left: `${hoop[0]}%`, top: `${hoop[1]}%` }} aria-label="골대 중심">골대</button>
      </div>
      <div className="setup-guide"><strong>확인 기준</strong><span>1→2→3→4 순서가 교차하지 않아야 합니다.</span><span>네 점은 한곳에 몰리지 않게 놓습니다.</span><span>골대 표시는 림 중심에 놓습니다.</span></div>
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="actions"><button className="button secondary" onClick={() => { setPoints(INITIAL_POINTS); setHoop([50, 18]) }}>초기화</button><button className="button primary" onClick={save} disabled={!analysis || saving}>{saving ? '저장 중…' : '확인하고 선수 설정으로'}</button></div>
    </section>
  </div>
}
