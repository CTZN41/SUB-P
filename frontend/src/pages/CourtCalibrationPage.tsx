import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

const API_BASE = 'http://127.0.0.1:8000/api'
const INITIAL_POINTS = [[12, 78], [88, 78], [72, 25], [28, 25]]
const INITIAL_COURT_POINTS = [[17, 0], [33, 0], [33, 19], [17, 19]]
const INITIAL_POINT_NAMES = ['베이스라인 왼쪽', '베이스라인 오른쪽', '자유투선 오른쪽', '자유투선 왼쪽']
const FULL_COURT_POINTS = [[0, 0], [50, 0], [50, 47], [0, 47]]
const FULL_COURT_NAMES = ['베이스라인 왼쪽 끝', '베이스라인 오른쪽 끝', '하프코트 오른쪽 끝', '하프코트 왼쪽 끝']

function matchesTemplate(points: number[][], template: number[][]) {
  return points.length === template.length && points.every((point, index) =>
    point.length === 2 && point.every((value, axis) => value === template[index][axis]))
}

function legacyLaneOrder(points: number[][]): number[] | null {
  const previous = [[19, 0], [31, 0], [31, 19], [19, 19]]
  const indexes = previous.map(([x, y]) => points.findIndex((point) => point[0] === x && point[1] === y))
  return indexes.every((index) => index >= 0) && new Set(indexes).size === 4 ? indexes : null
}

type Analysis = { id: string; title: string; duration_ms: number; width: number; height: number; play_type: string }

export function CourtCalibrationPage() {
  const { analysisId = '' } = useParams()
  const navigate = useNavigate()
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [timeMs, setTimeMs] = useState(0)
  const [points, setPoints] = useState(INITIAL_POINTS)
  const [courtPoints, setCourtPoints] = useState(INITIAL_COURT_POINTS)
  const [pointNames, setPointNames] = useState(INITIAL_POINT_NAMES)
  const [placedPoints, setPlacedPoints] = useState([false, false, false, false, false])
  const [hoop, setHoop] = useState([50, 18])
  const [draggingPoint, setDraggingPoint] = useState<number | null>(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [loadedFrameUrl, setLoadedFrameUrl] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [guideMessage, setGuideMessage] = useState('')
  const frameUrl = useMemo(() => `${API_BASE}/analyses/${analysisId}/calibration/frame?time_ms=${timeMs}`, [analysisId, timeMs])
  const frameReady = loadedFrameUrl === frameUrl
  const laneTemplate = matchesTemplate(courtPoints, INITIAL_COURT_POINTS)
  const fullCourtTemplate = matchesTemplate(courtPoints, FULL_COURT_POINTS)
  const supportedTemplate = laneTemplate || fullCourtTemplate
  const previousLaneOrder = legacyLaneOrder(courtPoints)
  const allPointsPlaced = placedPoints.every(Boolean)

  function chooseLaneTemplate() {
    setPoints(INITIAL_POINTS)
    setCourtPoints(INITIAL_COURT_POINTS)
    setPointNames(INITIAL_POINT_NAMES)
    setPlacedPoints([false, false, false, false, false])
    setConfirmed(false)
    setGuideMessage('기존 화면 점은 재사용하지 않습니다. 1~4번을 페인트존 선이 만나는 네 모서리에, 골대 점을 림 중심에 직접 맞춰 주세요.')
  }

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
      setPlacedPoints(matchesTemplate(saved.court_points, INITIAL_COURT_POINTS) || matchesTemplate(saved.court_points, FULL_COURT_POINTS) ? [true, true, true, true, true] : [false, false, false, false, false])
      setPointNames(matchesTemplate(saved.court_points, INITIAL_COURT_POINTS) ? INITIAL_POINT_NAMES : matchesTemplate(saved.court_points, FULL_COURT_POINTS) ? FULL_COURT_NAMES : ['기존 기준점 1', '기존 기준점 2', '기존 기준점 3', '기존 기준점 4'])
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
    setPlacedPoints((current) => current.map((placed, pointIndex) => pointIndex === index ? true : placed))
    setConfirmed(false)
  }

  async function save() {
    if (!analysis || !frameReady || !confirmed || !supportedTemplate || !allPointsPlaced) return
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

  return <div className="page setup-page">
    <header className="setup-header"><div><p className="eyebrow">COURT SETUP</p><h1>코트 위치 확인</h1><p className="lead">페인트존 또는 반코트 네 모서리를 선택하고, 영상의 해당 선 교차점과 골대 중심에 맞게 옮겨 주세요.</p></div><span className="step-chip">{analysis?.play_type === 'solo' ? '1 / 2 설정' : '1 / 3 설정'}</span></header>
    <section className="setup-card">
      <div className="frame-toolbar"><strong>{analysis?.title ?? '영상을 불러오는 중'}</strong><label>대표 장면<input type="range" min="0" max={Math.max(0, (analysis?.duration_ms ?? 0) - 500)} step="500" value={timeMs} onChange={(e) => { setTimeMs(Number(e.target.value)); setPlacedPoints([false, false, false, false, false]); setConfirmed(false); setError(''); setGuideMessage('장면이 바뀌었습니다. 네 모서리와 림을 다시 지정해 주세요.') }} /></label><span>{(timeMs / 1000).toFixed(1)}초</span></div>
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
      <div className="setup-guide"><strong>{supportedTemplate ? '확인 기준' : '기준 선택 필요'}</strong>{supportedTemplate ? <><span>{pointNames.map((name, index) => `${index + 1}: ${name}`).join(' · ')}</span>{laneTemplate && <span>1·2번은 골대 아래 끝선(베이스라인)과 페인트존 좌·우 선의 교차점, 3·4번은 자유투선과 페인트존 우·좌 선의 교차점에 맞추세요.</span>}<span>1→2→3→4 순서가 교차하지 않아야 합니다.</span><span>골대 표시는 림 중심에 놓습니다.</span></> : <span>아래 ‘페인트존 네 모서리’를 누른 뒤 1~4번을 실제 페인트존 선의 네 교차점에, 골대 점을 림 중심에 맞춰 주세요.</span>}</div>
      <p className="helper-text">안내용 점은 실제 코트 위치가 아닙니다. 선택한 네 모서리가 모두 보이는 장면에서 각 점을 코트 선이 만나는 곳에 직접 맞춰 주세요.</p>
      {!supportedTemplate && <p className="inline-warning">{previousLaneOrder ? '기존 기록의 기준은 현재 페인트존 경계와 달라 화면 점을 그대로 사용하지 않습니다.' : '이 기록의 가상 좌표는 현재 코트 경계 기준과 다릅니다.'} 아래 ‘페인트존 네 모서리’를 선택하고 네 점을 실제 페인트존 선의 교차점에 맞춘 뒤 림 중심도 지정해 주세요. 기존 분석 결과는 자동으로 바뀌지 않습니다.</p>}
      {!allPointsPlaced && <p className="inline-warning">네 모서리와 림을 각각 실제 위치에 지정해야 저장할 수 있습니다.</p>}
      {guideMessage && <p className="inline-warning">{guideMessage}</p>}
      <label className="check-row"><input type="checkbox" checked={confirmed} disabled={!allPointsPlaced || !supportedTemplate} onChange={(event) => setConfirmed(event.target.checked)} />영상에 반코트와 골대가 보이고 기준점을 실제 위치에 맞춘 것을 확인했습니다.</label>
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="actions"><button className="button secondary" onClick={chooseLaneTemplate}>페인트존 네 모서리</button><button className="button secondary" onClick={() => { setPoints(INITIAL_POINTS); setCourtPoints(FULL_COURT_POINTS); setPointNames(FULL_COURT_NAMES); setPlacedPoints([false, false, false, false, false]); setConfirmed(false); setGuideMessage('네 점을 반코트 전체 모서리에 직접 맞춰 주세요.') }}>반코트 네 모서리</button><button className="button primary" onClick={save} disabled={!analysis || !frameReady || !confirmed || saving || !supportedTemplate || !allPointsPlaced}>{saving ? '저장 중…' : analysis?.play_type === 'solo' ? '확인하고 분석 준비로' : '확인하고 선수 설정으로'}</button></div>
    </section>
  </div>
}
