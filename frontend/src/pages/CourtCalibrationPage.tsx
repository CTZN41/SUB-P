import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

const API_BASE = 'http://127.0.0.1:8000/api'
const INITIAL_POINTS = [[12, 78], [88, 78], [72, 25], [28, 25]]
const INITIAL_COURT_POINTS = [[0, 0], [50, 0], [50, 47], [0, 47]]
const INITIAL_POINT_NAMES = ['바닥 기준점 1', '바닥 기준점 2', '바닥 기준점 3', '바닥 기준점 4']

type Analysis = { id: string; title: string; duration_ms: number; width: number; height: number; play_type: string }

export function CourtCalibrationPage() {
  const { analysisId = '' } = useParams()
  const navigate = useNavigate()
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [timeMs, setTimeMs] = useState(0)
  const [points, setPoints] = useState(INITIAL_POINTS)
  const [courtPoints, setCourtPoints] = useState(INITIAL_COURT_POINTS)
  const [pointNames, setPointNames] = useState(INITIAL_POINT_NAMES)
  const [hoop, setHoop] = useState([50, 18])
  const [draggingPoint, setDraggingPoint] = useState<number | null>(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [loadedFrameUrl, setLoadedFrameUrl] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [detecting, setDetecting] = useState(false)
  const [suggestionMessage, setSuggestionMessage] = useState('')
  const frameUrl = useMemo(() => `${API_BASE}/analyses/${analysisId}/calibration/frame?time_ms=${timeMs}`, [analysisId, timeMs])
  const frameReady = loadedFrameUrl === frameUrl

  useEffect(() => {
    fetch(`${API_BASE}/analyses/${analysisId}`).then(async (response) => {
      if (!response.ok) throw new Error('분석 기록을 불러오지 못했습니다.')
      const details: Analysis = await response.json()
      setAnalysis(details)
      const savedResponse = await fetch(`${API_BASE}/analyses/${analysisId}/calibration`)
      if (savedResponse.status === 404) return
      if (!savedResponse.ok) throw new Error('저장된 코트 기준점을 불러오지 못했습니다.')
      const saved = await savedResponse.json()
      setPoints(saved.image_points.map(([x, y]: number[]) => [x / details.width * 100, y / details.height * 100]))
      setCourtPoints(saved.court_points)
      setHoop([saved.hoop_point[0] / details.width * 100, saved.hoop_point[1] / details.height * 100])
      setTimeMs(saved.frame_time_ms)
      setConfirmed(Boolean(saved.confirmed_at))
    }).catch((caught) => setError(caught.message))
  }, [analysisId])

  useEffect(() => {
    const image = new Image()
    image.onload = () => setLoadedFrameUrl(frameUrl)
    image.onerror = () => { setLoadedFrameUrl(''); setError('이 장면을 읽을 수 없습니다. 다른 시간을 선택해 주세요.') }
    image.src = frameUrl
    return () => { image.onload = null; image.onerror = null }
  }, [frameUrl])

  function movePoint(index: number, event: React.PointerEvent<HTMLDivElement>) {
    const rect = event.currentTarget.getBoundingClientRect()
    const next = [
      Math.max(0, Math.min(100, ((event.clientX - rect.left) / rect.width) * 100)),
      Math.max(0, Math.min(100, ((event.clientY - rect.top) / rect.height) * 100)),
    ]
    if (index === 4) setHoop(next)
    else setPoints((current) => current.map((point, pointIndex) => pointIndex === index ? next : point))
    setConfirmed(false)
  }

  async function save() {
    if (!analysis || !frameReady || !confirmed) return
    setSaving(true); setError('')
    const toPixels = (point: number[]) => [point[0] * analysis.width / 100, point[1] * analysis.height / 100]
    try {
      const response = await fetch(`${API_BASE}/analyses/${analysisId}/calibration`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ frame_time_ms: timeMs, image_points: points.map(toPixels), court_points: courtPoints, hoop_point: toPixels(hoop) }),
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(payload.detail?.message ?? '코트 위치를 저장하지 못했습니다.')
      navigate(analysis.play_type === 'solo' ? `/progress/${analysisId}` : `/players/${analysisId}`)
    } catch (caught) { setError(caught instanceof Error ? caught.message : '코트 위치를 저장하지 못했습니다.') }
    finally { setSaving(false) }
  }

  async function detect() {
    if (!analysis || !frameReady) return
    setDetecting(true); setError(''); setSuggestionMessage('')
    try {
      const response = await fetch(`${API_BASE}/analyses/${analysisId}/calibration/suggestion?time_ms=${timeMs}`)
      const payload = await response.json()
      if (!response.ok) throw new Error(payload.detail?.message ?? '코트 선을 자동으로 찾지 못했습니다.')
      if (!payload.usable || payload.image_points.length !== 4 || !payload.hoop_point) {
        setSuggestionMessage(payload.warnings?.join(' ') || '이 장면에서는 자동 기준점을 확정할 수 없습니다. 점을 직접 맞춰 주세요.')
        return
      }
      setPoints(payload.image_points.map(([x, y]: number[]) => [x / analysis.width * 100, y / analysis.height * 100]))
      setCourtPoints(payload.court_points)
      setPointNames(payload.landmark_names)
      setHoop([payload.hoop_point[0] / analysis.width * 100, payload.hoop_point[1] / analysis.height * 100])
      setConfirmed(false)
      setSuggestionMessage(`자동 기준점을 표시했습니다. 신뢰도 ${(payload.confidence * 100).toFixed(1)}% · 저장 전에 선 교차점과 골대 중심을 확인해 주세요.`)
    } catch (caught) { setError(caught instanceof Error ? caught.message : '코트 선을 자동으로 찾지 못했습니다.') }
    finally { setDetecting(false) }
  }

  return <div className="page setup-page">
    <header className="setup-header"><div><p className="eyebrow">COURT SETUP</p><h1>코트 위치 확인</h1><p className="lead">초기 점은 안내용입니다. 실제 바닥 모서리 네 곳과 골대 중심에 맞게 직접 눌러 옮겨 주세요.</p></div><span className="step-chip">{analysis?.play_type === 'solo' ? '1 / 2 설정' : '1 / 3 설정'}</span></header>
    <section className="setup-card">
      <div className="frame-toolbar"><strong>{analysis?.title ?? '영상을 불러오는 중'}</strong><label>대표 장면<input type="range" min="0" max={Math.max(0, (analysis?.duration_ms ?? 0) - 500)} step="500" value={timeMs} onChange={(e) => { setTimeMs(Number(e.target.value)); setConfirmed(false); setError(''); setSuggestionMessage('') }} /></label><span>{(timeMs / 1000).toFixed(1)}초</span><button className="button secondary" onClick={detect} disabled={!analysis || !frameReady || detecting}>{detecting ? '찾는 중…' : '코트 선 자동 찾기'}</button></div>
      <div className="calibration-frame" style={{ backgroundImage: frameReady ? `url(${frameUrl})` : 'none', aspectRatio: analysis ? `${analysis.width} / ${analysis.height}` : '16 / 9' }} onPointerDown={(event) => {
        const target = event.target as HTMLElement
        const index = Number(target.dataset.point)
        if (!Number.isNaN(index)) {
          event.currentTarget.setPointerCapture(event.pointerId)
          setDraggingPoint(index)
          movePoint(index, event)
        }
      }} onPointerMove={(event) => {
        if (draggingPoint !== null) movePoint(draggingPoint, event)
      }} onPointerUp={(event) => {
        if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
        setDraggingPoint(null)
      }} onPointerCancel={() => {
        setDraggingPoint(null)
      }}>
        <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><polygon points={points.map((point) => point.join(',')).join(' ')} /></svg>
        {points.map((point, index) => <button key={index} data-point={index} className="calibration-point" style={{ left: `${point[0]}%`, top: `${point[1]}%` }} aria-label={pointNames[index] ?? `바닥 기준점 ${index + 1}`} title={pointNames[index]}>{index + 1}</button>)}
        <button data-point="4" className="calibration-point hoop-point" style={{ left: `${hoop[0]}%`, top: `${hoop[1]}%` }} aria-label="골대 중심">골대</button>
      </div>
      <div className="setup-guide"><strong>확인 기준</strong><span>1→2→3→4 순서가 교차하지 않아야 합니다.</span><span>네 점은 한곳에 몰리지 않게 놓습니다.</span><span>골대 표시는 림 중심에 놓습니다.</span></div>
      <p className="helper-text">초기 점은 코트를 자동으로 찾은 결과가 아닙니다. 실제 코트가 보이지 않으면 장면을 바꾸고, 보이는 코트 선에 점을 직접 맞춰 주세요.</p>
      {suggestionMessage && <p className="inline-warning">{suggestionMessage}</p>}
      <label className="check-row"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />영상에 반코트와 골대가 보이고 기준점을 실제 위치에 맞춘 것을 확인했습니다.</label>
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="actions"><button className="button secondary" onClick={() => { setPoints(INITIAL_POINTS); setCourtPoints(INITIAL_COURT_POINTS); setPointNames(INITIAL_POINT_NAMES); setHoop([50, 18]); setConfirmed(false); setSuggestionMessage('') }}>초기화</button><button className="button primary" onClick={save} disabled={!analysis || !frameReady || !confirmed || saving}>{saving ? '저장 중…' : analysis?.play_type === 'solo' ? '확인하고 분석 준비로' : '확인하고 선수 설정으로'}</button></div>
    </section>
  </div>
}
