import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { AnalysisBackLink } from '../components/AnalysisBackLink'

const API_BASE = '/api'
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
type SavedView = { id: string; frame_time_ms: number; image_points: number[][]; court_points: number[][]; hoop_point: number[]; confirmed_at: string | null }
type LandmarkQuality = { point_index: number; usable: boolean; reason: string }
type CalibrationQuality = { landmarks: LandmarkQuality[]; usable_count: number; required_count: number }

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
  const [views, setViews] = useState<SavedView[]>([])
  const [quality, setQuality] = useState<{ key: string; report?: CalibrationQuality; error?: string } | null>(null)
  const frameUrl = useMemo(() => `${API_BASE}/analyses/${analysisId}/calibration/frame?time_ms=${timeMs}`, [analysisId, timeMs])
  const frameReady = loadedFrameUrl === frameUrl
  const laneTemplate = matchesTemplate(courtPoints, INITIAL_COURT_POINTS)
  const fullCourtTemplate = matchesTemplate(courtPoints, FULL_COURT_POINTS)
  const supportedTemplate = laneTemplate || fullCourtTemplate
  const previousLaneOrder = legacyLaneOrder(courtPoints)
  const allPointsPlaced = placedPoints.every(Boolean)
  const missingPointNames = [...pointNames, '림 중심'].filter((_, index) => !placedPoints[index])
  const qualityRequest = useMemo(() => analysis && allPointsPlaced && supportedTemplate ? JSON.stringify({
    frame_time_ms: timeMs,
    image_points: points.map(([x, y]) => [x * analysis.width / 100, y * analysis.height / 100]),
    court_points: courtPoints,
    hoop_point: [hoop[0] * analysis.width / 100, hoop[1] * analysis.height / 100],
  }) : null, [analysis, allPointsPlaced, supportedTemplate, timeMs, points, courtPoints, hoop])
  const qualityKey = `${analysisId}:${qualityRequest}`
  const currentQuality = qualityRequest && quality?.key === qualityKey ? quality : null

  function resetCalibration() {
    setPoints(INITIAL_POINTS)
    setCourtPoints(INITIAL_COURT_POINTS)
    setPointNames(INITIAL_POINT_NAMES)
    setPlacedPoints([false, false, false, false, false])
    setHoop([50, 18])
    setDraggingPoint(null)
    setError('')
    setConfirmed(false)
    setGuideMessage('기준점을 재설정했습니다. 페인트존 네 모서리와 림 중심을 다시 맞춰 주세요.')
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
      const list = await fetch(`${API_BASE}/analyses/${analysisId}/calibrations`)
      if (list.ok) setViews(await list.json())
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

  useEffect(() => {
    if (!qualityRequest || !frameReady || draggingPoint !== null) return
    const controller = new AbortController()
    const timer = window.setTimeout(async () => {
      try {
        const response = await fetch(`${API_BASE}/analyses/${analysisId}/calibration/quality`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: qualityRequest, signal: controller.signal,
        })
        const payload = await response.json()
        if (!response.ok) throw new Error(payload.detail?.message ?? '기준점 품질을 확인하지 못했습니다.')
        if (!controller.signal.aborted) setQuality({ key: qualityKey, report: payload })
      } catch (caught) {
        if (!controller.signal.aborted) setQuality({ key: qualityKey,
          error: caught instanceof Error ? caught.message : '기준점 품질을 확인하지 못했습니다.' })
      }
    }, 350)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [analysisId, qualityRequest, qualityKey, frameReady, draggingPoint])

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

  async function save(continueAfter = true) {
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
      if (continueAfter) navigate(analysis.play_type === 'solo' ? `/progress/${analysisId}` : `/players/${analysisId}`)
      else { const list = await fetch(`${API_BASE}/analyses/${analysisId}/calibrations`); if (list.ok) setViews(await list.json()); setGuideMessage('이 구도를 저장했습니다. 다른 구도의 장면으로 이동해 추가로 지정할 수 있습니다.') }
    } catch (caught) { setError(caught instanceof Error ? caught.message : '코트 위치를 저장하지 못했습니다.') }
    finally { setSaving(false) }
  }

  return <div className="page setup-page">
    <AnalysisBackLink to="/history" />
    <header className="setup-header"><div><p className="eyebrow">COURT SETUP</p><h1>코트 위치 확인</h1><p className="lead">네 모서리와 골대 점을 영상의 실제 코트 선과 림 중심에 맞춰 주세요.</p></div><span className="step-chip">{analysis?.play_type === 'solo' ? '1 / 2 설정' : '1 / 3 설정'}</span></header>
    <section className="setup-card">
      <label>저장한 구도<select value={views.some(v => v.frame_time_ms === timeMs) ? timeMs : ''} onChange={e => {
        const view = views.find(v => v.frame_time_ms === Number(e.target.value)); if (!view || !analysis) return
        setTimeMs(view.frame_time_ms); setPoints(view.image_points.map(([x,y]) => [x/analysis.width*100,y/analysis.height*100])); setCourtPoints(view.court_points)
        setPointNames(matchesTemplate(view.court_points, INITIAL_COURT_POINTS) ? INITIAL_POINT_NAMES : FULL_COURT_NAMES)
        setHoop([view.hoop_point[0]/analysis.width*100,view.hoop_point[1]/analysis.height*100]); setPlacedPoints([true,true,true,true,true]); setConfirmed(!!view.confirmed_at)
      }}><option value="">새 구도 지정</option>{views.map(v => <option key={v.id} value={v.frame_time_ms}>{(v.frame_time_ms/1000).toFixed(1)}초 구도</option>)}</select></label>
      <div className="frame-toolbar"><strong>{analysis?.title ?? '영상을 불러오는 중'}</strong><label>대표 장면<input type="range" min="0" max={Math.max(0, (analysis?.duration_ms ?? 0) - 500)} step="500" value={timeMs} onChange={(e) => { setTimeMs(Number(e.target.value)); setPlacedPoints([false, false, false, false, false]); setConfirmed(false); setError(''); setGuideMessage('') }} /></label><span>{(timeMs / 1000).toFixed(1)}초</span></div>
      <div className="calibration-frame" style={{ backgroundImage: frameReady ? `url(${frameUrl})` : 'none', aspectRatio: analysis ? `${analysis.width} / ${analysis.height}` : '16 / 9' }} onPointerDown={(event) => {
        if (!frameReady) return
        const marker = (event.target as Element).closest<HTMLElement>('[data-point]')
        if (marker) {
          const index = Number(marker.dataset.point)
          event.currentTarget.setPointerCapture(event.pointerId)
          setDraggingPoint(index)
          movePoint(index, event)
          return
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
      <div className="setup-guide calibration-guide">
        <strong>{supportedTemplate ? '확인 기준' : '재설정 필요'}</strong>
        {supportedTemplate ? <>
          {laneTemplate ? <>
            <span><b>1번 · 베이스라인 왼쪽</b> — 골대 아래 끝선과 페인트존 왼쪽 선의 교차점</span>
            <span><b>2번 · 베이스라인 오른쪽</b> — 골대 아래 끝선과 페인트존 오른쪽 선의 교차점</span>
            <span><b>3번 · 자유투선 오른쪽</b> — 자유투선과 페인트존 오른쪽 선의 교차점</span>
            <span><b>4번 · 자유투선 왼쪽</b> — 자유투선과 페인트존 왼쪽 선의 교차점</span>
          </> : pointNames.map((name, index) => <span key={name}>{index + 1}번 · {name}</span>)}
          <span><b>골대 점</b> — 림 중심</span>
          <span>1 → 2 → 3 → 4 순서가 교차하지 않도록 맞춰 주세요.</span>
        </> : <span>‘재설정’을 누른 뒤 페인트존 네 모서리와 림 중심을 맞춰 주세요.</span>}
      </div>
      {!supportedTemplate && <p className="inline-warning"><span className="message-line">{previousLaneOrder ? '기존 기록의 기준은 현재 페인트존 경계와 달라 화면 점을 그대로 사용하지 않습니다.' : '이 기록의 가상 좌표는 현재 코트 경계 기준과 다릅니다.'}</span><span className="message-line">‘재설정’을 누른 뒤 페인트존 네 모서리와 림 중심을 다시 지정해 주세요.</span><span className="message-line">기존 분석 결과는 자동으로 바뀌지 않습니다.</span></p>}
      {guideMessage && <p className="inline-warning">{guideMessage}</p>}
      {currentQuality?.report && currentQuality.report.usable_count < 4 && <div className="inline-warning" role="status">
        <strong>주변 영상 특징을 비교할 수 있는 기준점 {currentQuality.report.usable_count}/4개</strong>
        <ul className="message-list">{currentQuality.report.landmarks.filter(point => !point.usable).map(point => <li key={point.point_index}>
          {point.point_index}번 · {pointNames[point.point_index - 1]} — {point.reason === 'image_edge' ? '화면 끝에 가까워 주변 선을 비교하기 어렵습니다.' : '주변 선이나 바닥 특징이 흐려 비교하기 어렵습니다.'}
        </li>)}</ul>
        <span className="message-line">같은 교차점이 선명하고 화면 끝에서 떨어져 보이는 장면을 선택해 주세요. 구도 검증에는 최소 {currentQuality.report.required_count}개가 필요합니다.</span>
      </div>}
      {currentQuality?.error && <p className="inline-warning" role="status">{currentQuality.error}</p>}
      {!allPointsPlaced && <div className="inline-warning" role="status"><strong>아직 확인하지 않은 기준점</strong><ul className="message-list">{missingPointNames.map(name => <li key={name}>{name}</li>)}</ul><span className="message-line">해당 점을 실제 위치로 옮기거나, 이미 맞다면 한 번 눌러 주세요.</span></div>}
      <label className="check-row"><input type="checkbox" checked={confirmed} disabled={!allPointsPlaced || !supportedTemplate} onChange={(event) => setConfirmed(event.target.checked)} /><span>영상에 반코트와 골대가 보이고 기준점을 실제 위치에 맞춘 것을 확인했습니다.</span></label>
      {allPointsPlaced && supportedTemplate && !confirmed && <p role="status">위 확인란을 체크하면 저장할 수 있습니다.</p>}
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="actions"><button type="button" className="button secondary" onClick={resetCalibration} disabled={saving}>재설정</button><button type="button" className="button secondary" onClick={() => save(false)} disabled={!analysis || !frameReady || !confirmed || saving || !supportedTemplate || !allPointsPlaced}>이 구도 저장</button><button className="button primary" onClick={() => save()} disabled={!analysis || !frameReady || !confirmed || saving || !supportedTemplate || !allPointsPlaced}>{saving ? '저장 중…' : analysis?.play_type === 'solo' ? '확인하고 분석 준비로' : '확인하고 선수 설정으로'}</button></div>
    </section>
  </div>
}
