import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useLocation, useParams } from 'react-router-dom'
import { ShotHeatmap } from '../components/ShotHeatmap'
import { ShotPositionPicker } from '../components/ShotPositionPicker'
import { ShotResultSummary } from '../components/ShotResultSummary'
import { AnalysisBackLink } from '../components/AnalysisBackLink'
import { hasShotPosition, shotCategory, shotNumbers, formatShotTime, parseShotTime, shouldShowPlayerNames, SHOT_CATEGORY_LABELS } from '../utils/shotPresentation'
import type { ShotCategory } from '../utils/shotPresentation'

const ShotCourt3D = lazy(() => import('../components/ShotCourt3D').then((module) => ({ default: module.ShotCourt3D })))

const API_BASE = '/api'
const SHOT_TYPES = [
  ['catch_and_shoot', '캐치 앤 슛'], ['set_shot', '제자리 슛'],
  ['dribble_pull_up', '드리블 풀업'], ['side_step', '사이드 스텝'], ['layup', '레이업'], ['dunk', '덩크'],
  ['fadeaway', '페이더웨이'], ['step_back', '스텝백'],
  ['turnaround', '턴어라운드'], ['hook_shot', '훅슛'], ['other', '기타'],
]
const TYPE_LABEL = Object.fromEntries(SHOT_TYPES)

type Analysis = { id: string; title: string; duration_ms: number; fps: number | null; play_type: string }
type Player = { id: string; display_name: string; jersey_number: string | null }
type Shot = { id: string; player_id: string; player_name: string; candidate_time_ms: number; sequence_start_ms: number; sequence_end_ms: number; court_x: number | null; court_y: number | null; zone_code: string | null; zone_label: string | null; result: string; shot_type: string; status: string; review_reason: string | null; source: string; confidence_data?: { shot_gate?: { verdict?: string } } }
type StatLine = { attempts: number; made: number; missed: number; success_rate: number | null; attempt_share: number | null; difference_from_overall: number | null }
type Stats = { overall: StatLine; by_player: Record<string, StatLine>; by_shot_type: Record<string, StatLine>; by_zone: Record<string, StatLine> }
type ShotList = { analysis_status: string; duration_ms: number; fps: number | null; shots: Shot[]; statistics: Stats }
type FormState = { player_id: string; candidate_time_ms: string; sequence_start_ms: string; sequence_end_ms: string; court_x: string; court_y: string; result: string; shot_type: string }

function milliseconds(value: string, original?: number) {
  const parsed = parseShotTime(value, original)
  if (parsed === null) throw new Error('시간은 08:26처럼 분:초로 입력해 주세요.')
  return parsed
}
function readableReason(shot: Shot) {
  const reasons: string[] = []
  if (shot.result !== 'made' && shot.result !== 'missed') reasons.push('성공·실패를 영상에서 확정하지 못했습니다.')
  if (!hasShotPosition(shot)) reasons.push('슛 위치를 확정하지 못했습니다.')
  if (reasons.length) return reasons.join(' ')
  if (shot.shot_type === 'needs_review') return '슛 종류를 확정하지 못했습니다.'
  return '자동 판정을 확인해 주세요.'
}
function shotToForm(shot: Shot): FormState {
  return { player_id: shot.player_id, candidate_time_ms: formatShotTime(shot.candidate_time_ms), sequence_start_ms: formatShotTime(shot.sequence_start_ms), sequence_end_ms: formatShotTime(shot.sequence_end_ms), court_x: shot.court_x?.toFixed(2) ?? '', court_y: shot.court_y?.toFixed(2) ?? '', result: shot.result === 'needs_review' ? '' : shot.result, shot_type: shot.shot_type === 'needs_review' ? '' : shot.shot_type }
}

function possibleNonShot(shot: Shot) {
  return shot.status === 'needs_review' && shot.confidence_data?.shot_gate?.verdict === 'not_shot'
}

function statLine(shots: Shot[], totalAttempts?: number, overallRate?: number | null): StatLine {
  const made = shots.filter((shot) => shotCategory(shot) === 'made').length
  const missed = shots.filter((shot) => shotCategory(shot) === 'missed').length
  const successRate = made + missed ? made / (made + missed) * 100 : null
  return {
    attempts: shots.length, made, missed, success_rate: successRate,
    attempt_share: totalAttempts ? shots.length / totalAttempts * 100 : null,
    difference_from_overall: successRate != null && overallRate != null ? successRate - overallRate : null,
  }
}

function shotStatistics(shots: Shot[], zones: string[]): Stats {
  const overall = statLine(shots)
  const byPlayer: Record<string, StatLine> = {}
  const byShotType: Record<string, StatLine> = {}
  const byZone: Record<string, StatLine> = {}
  for (const id of new Set(shots.map((shot) => shot.player_id))) byPlayer[id] = statLine(shots.filter((shot) => shot.player_id === id))
  for (const type of new Set(shots.map((shot) => shot.shot_type))) byShotType[type] = statLine(shots.filter((shot) => shot.shot_type === type))
  for (const zone of zones) byZone[zone] = statLine(shots.filter((shot) => shot.zone_code === zone), overall.attempts, overall.success_rate)
  return { overall, by_player: byPlayer, by_shot_type: byShotType, by_zone: byZone }
}

function filteredShotList(source: ShotList, playerId: string, result: string, shotType: string): ShotList {
  const shots = source.shots.filter((shot) =>
    (!playerId || shot.player_id === playerId) && (!result || shotCategory(shot) === result) && (!shotType || shot.shot_type === shotType))
  return { ...source, shots, statistics: shotStatistics(shots, Object.keys(source.statistics.by_zone)) }
}

export function ShotReviewPage() {
  const { analysisId = '' } = useParams()
  const { search } = useLocation()
  const requestedShotId = new URLSearchParams(search).get('shot')
  const videoRef = useRef<HTMLVideoElement>(null)
  const courtRef = useRef<HTMLDivElement>(null)
  const addFormRef = useRef<HTMLFormElement>(null)
  const sequenceEnd = useRef<number | null>(null)
  const sequenceStart = useRef<number | null>(null)
  const openedDeepLink = useRef<string | null>(null)
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [players, setPlayers] = useState<Player[]>([])
  const [sourceData, setSourceData] = useState<ShotList | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState<FormState | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [playerFilter, setPlayerFilter] = useState('')
  const [resultFilter, setResultFilter] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [selectedZone, setSelectedZone] = useState<string | null>(null)
  const [showShotList, setShowShotList] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const data = useMemo(() => sourceData ? filteredShotList(sourceData, playerFilter, resultFilter, typeFilter) : null, [sourceData, playerFilter, resultFilter, typeFilter])
  const selected = data?.shots.find((shot) => shot.id === selectedId) ?? null
  const showPlayerNames = shouldShowPlayerNames(analysis?.play_type ?? 'solo', players.map(player => player.display_name))
  const numbers = useMemo(() => shotNumbers(sourceData?.shots ?? []), [sourceData?.shots])

  const load = useCallback(async () => {
    const [analysisResponse, playerResponse, shotResponse] = await Promise.all([fetch(`${API_BASE}/analyses/${analysisId}`), fetch(`${API_BASE}/analyses/${analysisId}/players`), fetch(`${API_BASE}/analyses/${analysisId}/shots`)])
    const analysisPayload = await analysisResponse.json(); const playerPayload = await playerResponse.json(); const shotPayload = await shotResponse.json()
    if (!analysisResponse.ok || !playerResponse.ok || !shotResponse.ok) throw new Error(shotPayload.detail?.message ?? '슛 기록을 불러오지 못했습니다.')
    setAnalysis(analysisPayload); setPlayers(playerPayload.players); setSourceData(shotPayload)
  }, [analysisId])

  useEffect(() => { load().catch((caught) => setError(caught.message)) }, [load])
  useEffect(() => { setSelectedZone(null) }, [playerFilter, resultFilter, typeFilter])
  useEffect(() => { if (data && selectedId && !data.shots.some((shot) => shot.id === selectedId)) { videoRef.current?.pause(); sequenceEnd.current = null; sequenceStart.current = null; setSelectedId(null); setForm(null) } }, [data, selectedId])
  useEffect(() => { if (selected && !adding) setForm(shotToForm(selected)) }, [selected, adding])
  useEffect(() => {
    if (!adding || !showShotList) return
    const frame = requestAnimationFrame(() => addFormRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
    return () => cancelAnimationFrame(frame)
  }, [adding, showShotList])
  useEffect(() => {
    if (!data || !requestedShotId || openedDeepLink.current === requestedShotId) return
    const shot = data.shots.find((item) => item.id === requestedShotId)
    if (shot) { openedDeepLink.current = requestedShotId; playShot(shot) }
  }, [data, requestedShotId])

  function choose(shot: Shot) {
    setShowAdvanced(shot.court_x == null || shot.court_y == null)
    setAdding(false); setSelectedId(shot.id); setForm(shotToForm(shot)); setError(''); setNotice('')
    sequenceEnd.current = shot.sequence_end_ms / 1000
    sequenceStart.current = shot.sequence_start_ms / 1000
    if (videoRef.current) videoRef.current.currentTime = shot.sequence_start_ms / 1000
  }
  function startAdding() {
    videoRef.current?.pause(); sequenceEnd.current = null; sequenceStart.current = null
    setShowAdvanced(false)
    const duration = analysis?.duration_ms ?? data?.duration_ms ?? 10_000
    const candidate = selected?.candidate_time_ms ?? Math.max(0, Math.round(duration / 2))
    const start = Math.max(0, candidate - 2000); const end = Math.min(duration, Math.max(candidate + 2000, start + 1))
    setAdding(true); setSelectedId(null); setError(''); setNotice('')
    setForm({ player_id: players[0]?.id ?? '', candidate_time_ms: formatShotTime(candidate), sequence_start_ms: formatShotTime(start), sequence_end_ms: formatShotTime(end), court_x: '25', court_y: '15', result: 'made', shot_type: 'set_shot' })
  }
  function playShot(shot: Shot) {
    choose(shot)
    setShowShotList(true)
    const video = videoRef.current
    if (!video) return
    video.currentTime = shot.sequence_start_ms / 1000
    sequenceEnd.current = shot.sequence_end_ms / 1000
    video.play().catch(() => setError('자동 재생이 차단됐습니다. 영상의 재생 버튼을 눌러 주세요.'))
    requestAnimationFrame(() => video.scrollIntoView({ behavior: 'smooth', block: 'center' }))
  }
  function onVideoTime() {
    const video = videoRef.current
    if (video && !video.paused && sequenceEnd.current !== null && sequenceStart.current !== null && video.currentTime >= sequenceEnd.current) {
      video.currentTime = sequenceStart.current
    }
  }
  function onVideoEnded() {
    const video = videoRef.current
    if (!video || sequenceStart.current === null || sequenceEnd.current === null) return
    video.currentTime = sequenceStart.current
    video.play().catch(() => setError('반복 재생이 차단됐습니다. 영상의 재생 버튼을 눌러 주세요.'))
  }
  function change(field: keyof FormState, value: string) { setForm((current) => current ? { ...current, [field]: value } : current) }

  async function save(event: FormEvent) {
    event.preventDefault(); if (!form) return
    if (!form.court_x.trim() || !form.court_y.trim() || !hasShotPosition({ id: 'form', result: form.result, court_x: Number(form.court_x), court_y: Number(form.court_y) })) {
      setShowAdvanced(true)
      setError('2D 코트에서 슛 위치를 선택해 주세요.')
      return
    }
    setBusy(true); setError('')
    try {
      const payload = { player_id: form.player_id, candidate_time_ms: milliseconds(form.candidate_time_ms, adding ? undefined : selected?.candidate_time_ms), sequence_start_ms: milliseconds(form.sequence_start_ms, adding ? undefined : selected?.sequence_start_ms), sequence_end_ms: milliseconds(form.sequence_end_ms, adding ? undefined : selected?.sequence_end_ms), court_x: Number(form.court_x), court_y: Number(form.court_y), result: form.result, shot_type: form.shot_type }
      const url = adding ? `${API_BASE}/analyses/${analysisId}/shots` : `${API_BASE}/analyses/${analysisId}/shots/${selectedId}`
      const response = await fetch(url, { method: adding ? 'POST' : 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      const result = await response.json(); if (!response.ok) throw new Error(result.detail?.message ?? '슛 기록을 저장하지 못했습니다.')
      const savedShot = result as Shot
      if ((resultFilter === 'position_review' || resultFilter === 'outcome_review') && sourceData) {
        const updated = [...sourceData.shots.filter(shot => shot.id !== savedShot.id), savedShot]
        if (!updated.some(shot => shotCategory(shot) === resultFilter)) setResultFilter('')
      }
      setSourceData((current) => {
        if (!current) return current
        const shots = [...current.shots.filter((shot) => shot.id !== savedShot.id), savedShot].sort((a, b) => a.candidate_time_ms - b.candidate_time_ms)
        return { ...current, shots, analysis_status: shots.some((shot) => shot.status === 'needs_review') ? 'review_required' : 'completed', statistics: shotStatistics(shots, Object.keys(current.statistics.by_zone)) }
      })
      setAdding(false); setSelectedId(savedShot.id); setNotice('슛 기록을 저장했습니다.')
    } catch (caught) { setError(caught instanceof Error ? caught.message : '슛 기록을 저장하지 못했습니다.') } finally { setBusy(false) }
  }
  async function remove() {
    if (!selected || !window.confirm(`${formatShotTime(selected.candidate_time_ms)} 슛 기록을 삭제할까요?`)) return
    setBusy(true); setError('')
    try {
      const response = await fetch(`${API_BASE}/analyses/${analysisId}/shots/${selected.id}`, { method: 'DELETE' })
      if (!response.ok) { const result = await response.json(); throw new Error(result.detail?.message ?? '삭제하지 못했습니다.') }
      videoRef.current?.pause(); sequenceEnd.current = null; sequenceStart.current = null
      if ((resultFilter === 'position_review' || resultFilter === 'outcome_review') && sourceData &&
          !sourceData.shots.some(shot => shot.id !== selected.id && shotCategory(shot) === resultFilter)) setResultFilter('')
      setSourceData((current) => {
        if (!current) return current
        const shots = current.shots.filter((shot) => shot.id !== selected.id)
        return { ...current, shots, analysis_status: shots.some((shot) => shot.status === 'needs_review') ? 'review_required' : 'completed', statistics: shotStatistics(shots, Object.keys(current.statistics.by_zone)) }
      })
      setSelectedId(null); setForm(null)
    } catch (caught) { setError(caught instanceof Error ? caught.message : '삭제하지 못했습니다.') } finally { setBusy(false) }
  }

  const visibleShots = selectedZone ? data?.shots.filter((shot) => shot.zone_code === selectedZone) ?? [] : data?.shots ?? []
  const selectedZoneLabel = selectedZone ? visibleShots[0]?.zone_label ?? '선택한 구역' : null
  function selectZone(zone: string) {
    const nextZone = selectedZone === zone ? null : zone
    setSelectedZone(nextZone)
    requestAnimationFrame(() => courtRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
  }
  if (!data) return <div className="page shot-review-page"><p role="status" className="inline-warning">{error || '분석 결과를 불러오는 중입니다…'}</p></div>
  return <div className="page shot-review-page">
    <AnalysisBackLink to="/history" />
    <header className="setup-header"><div><p className="eyebrow">분석 결과</p><h1>{analysis?.title ?? '분석 결과'}</h1>{analysis && analysis.play_type !== 'solo' && <p className="inline-warning">다인원 자동 추적은 아직 지원하지 않습니다. 원본 영상을 보면서 슛과 선수를 직접 기록해 주세요.</p>}</div></header>
    <ShotResultSummary shots={sourceData?.shots ?? []} selectedCategory={resultFilter ? resultFilter as ShotCategory : null} onSelect={(category) => { setResultFilter(category ?? ''); setPlayerFilter(''); setTypeFilter(''); setSelectedZone(null); setShowShotList(true) }} />
    <section className="setup-card results-dashboard">
      <div className="panel-title"><div><h2>14개 구역 히트맵</h2></div></div>
      {showShotList && <div className="result-filters" aria-label="결과 필터">
        <label>선수<select value={playerFilter} onChange={(event) => setPlayerFilter(event.target.value)}><option value="">전체 선수</option>{players.map((player) => <option key={player.id} value={player.id}>{player.display_name}{player.jersey_number ? ` #${player.jersey_number}` : ''}</option>)}</select></label>
        <label>기록 구분<select value={resultFilter} onChange={(event) => setResultFilter(event.target.value)}><option value="">전체</option>{Object.entries(SHOT_CATEGORY_LABELS).filter(([value]) => value === 'made' || value === 'missed' || sourceData?.shots.some(shot => shotCategory(shot) === value)).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>슛 종류<select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}><option value="">전체 종류</option>{SHOT_TYPES.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
        <button className="button secondary" type="button" onClick={() => { setPlayerFilter(''); setResultFilter(''); setTypeFilter(''); setSelectedZone(null) }}>필터 초기화</button>
      </div>}
      <div className="results-visual-layout">
        <ShotHeatmap statistics={data?.statistics.by_zone ?? {}} selectedZone={selectedZone} onSelect={selectZone} />
        {showShotList && <div className="stat-breakdowns">
          <section><h3>선수별</h3>{Object.entries(data?.statistics.by_player ?? {}).length ? <ul>{Object.entries(data?.statistics.by_player ?? {}).map(([id, line]) => <li key={id}><span>{players.find((player) => player.id === id)?.display_name ?? '선수'}</span><strong>{line.made}/{line.attempts} · {line.success_rate == null ? '-' : `${line.success_rate.toFixed(1)}%`}</strong></li>)}</ul> : <p>기록 없음</p>}</section>
          <section><h3>슛 종류별</h3>{Object.entries(data?.statistics.by_shot_type ?? {}).length ? <ul>{Object.entries(data?.statistics.by_shot_type ?? {}).map(([type, line]) => <li key={type}><span>{TYPE_LABEL[type] ?? (type === 'needs_review' ? '확인 필요' : type)}</span><strong>{line.made}/{line.attempts} · {line.success_rate == null ? '-' : `${line.success_rate.toFixed(1)}%`}</strong></li>)}</ul> : <p>기록 없음</p>}</section>
        </div>}
      </div>
    </section>
    <div ref={courtRef} style={{ scrollMarginTop: 24 }}><Suspense fallback={<p className="inline-warning">3D 코트를 불러오는 중입니다…</p>}><ShotCourt3D
      shots={visibleShots}
      numbers={numbers}
      showPlayerName={showPlayerNames}
      selectedShot={selected}
      jerseyNumber={selected ? players.find((player) => player.id === selected.player_id)?.jersey_number ?? null : null}
      onSelect={(id) => { const shot = data?.shots.find((item) => item.id === id); if (shot) playShot(shot) }}
      onOpenDetails={() => {
        if (showShotList) { videoRef.current?.pause(); sequenceEnd.current = null; sequenceStart.current = null; setSelectedId(null) }
        setShowShotList((open) => !open)
      }}
      detailsOpen={showShotList}
      typeLabels={TYPE_LABEL}
      zone={selectedZone}
      onZoneChange={setSelectedZone}
    /></Suspense></div>
    <div className="review-layout" hidden={!showShotList}>
      {showShotList && <section className="setup-card shot-list-panel"><div className="panel-title"><div><h2>{selectedZoneLabel ? `${selectedZoneLabel} 슛 기록` : resultFilter ? `${SHOT_CATEGORY_LABELS[resultFilter as ShotCategory]} 슛 기록` : '전체 슛 기록'}</h2>{visibleShots.some((shot) => shot.status === 'needs_review' || shotCategory(shot) === 'position_review' || shotCategory(shot) === 'outcome_review') && <p>확인이 필요한 기록이 있습니다.</p>}</div><div className="actions"><button className="button primary" onClick={startAdding} disabled={!players.length}>{analysis?.play_type !== 'solo' ? '슛 직접 추가' : '빠진 슛 추가'}</button><button className="button secondary" onClick={() => { videoRef.current?.pause(); sequenceEnd.current = null; sequenceStart.current = null; setShowShotList(false); setSelectedId(null) }}>닫기</button></div></div>
        {!visibleShots.length && <p className="inline-warning">현재 조건에 맞는 슛이 없습니다.{!selectedZone && ' 빠진 슛을 직접 추가할 수 있습니다.'}</p>}
        <ol className="review-shot-list">{visibleShots.map((shot) => <li key={shot.id}><button className={selected?.id === shot.id ? 'selected' : ''} onClick={() => playShot(shot)}><span className={`result-mark ${shotCategory(shot)}`}>{numbers[shot.id]}</span><span><strong>{numbers[shot.id]}번 · {formatShotTime(shot.candidate_time_ms)}{showPlayerNames ? ` · ${shot.player_name}` : ''}</strong><small>{TYPE_LABEL[shot.shot_type] ?? '슛 종류 확인 필요'} / {shot.zone_label ?? '위치 확인 필요'}</small></span></button></li>)}</ol>
      </section>}
      <section className="setup-card shot-detail-panel">
        <video ref={videoRef} src={`${API_BASE}/analyses/${analysisId}/video`} controls preload="metadata" onTimeUpdate={onVideoTime} onEnded={onVideoEnded} />
        {selected && (!hasShotPosition(selected) || shotCategory(selected) === 'outcome_review' || selected.status === 'needs_review') && !adding && <p className="inline-warning"><strong>확인할 이유:</strong> {readableReason(selected)}</p>}
        {selected && possibleNonShot(selected) && !adding && <p className="helper-text">원본을 보고 슛이 맞으면 아래에서 결과·종류·위치를 선택해 저장하세요. 슛이 아니면 이 후보 기록을 삭제할 수 있습니다.</p>}
        {!form && <p className="helper-text">왼쪽에서 슛을 선택하거나 빠진 슛을 추가해 주세요.</p>}
        {form && <form ref={addFormRef} className="shot-edit-form" onSubmit={save} onInvalidCapture={() => { setShowAdvanced(true); setError('시간은 분:초 형식으로 입력하고, 결과·종류와 코트 위치를 확인해 주세요.') }}><h2>{adding ? '빠진 슛 추가' : '선택한 슛 확인·수정'}</h2>
          <button className="button secondary shot-advanced-toggle" type="button" aria-expanded={showAdvanced} aria-controls="shot-advanced-fields" onClick={() => setShowAdvanced((value) => !value)}>{showAdvanced ? '시간·코트 위치 닫기' : '시간·코트 위치 수정'}</button>
          <div className="shot-form-grid"><label>선수<select value={form.player_id} onChange={(e) => change('player_id', e.target.value)}>{players.map((player) => <option value={player.id} key={player.id}>{player.display_name}{player.jersey_number ? ` #${player.jersey_number}` : ''}</option>)}</select></label><label>슛 시간(분:초)<input type="text" placeholder="08:26" pattern="[0-9]+:[0-5][0-9]" value={form.candidate_time_ms} onChange={(e) => change('candidate_time_ms', e.target.value)} required /></label><label>결과<select value={form.result} onChange={(e) => change('result', e.target.value)} required><option value="" disabled>결과 선택</option><option value="made">성공</option><option value="missed">실패</option></select></label><label>슛 종류<select value={form.shot_type} onChange={(e) => change('shot_type', e.target.value)} required><option value="" disabled>종류 선택</option>{SHOT_TYPES.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>{showAdvanced && <><label>장면 시작(분:초)<input type="text" placeholder="08:24" pattern="[0-9]+:[0-5][0-9]" value={form.sequence_start_ms} onChange={(e) => change('sequence_start_ms', e.target.value)} required /></label><label>장면 끝(분:초)<input type="text" placeholder="08:29" pattern="[0-9]+:[0-5][0-9]" value={form.sequence_end_ms} onChange={(e) => change('sequence_end_ms', e.target.value)} required /></label></>}</div>
          {showAdvanced && <div id="shot-advanced-fields" className="shot-advanced-fields">
            <ShotPositionPicker x={form.court_x} y={form.court_y} disabled={busy} saved={!adding && selected?.court_x != null && selected?.court_y != null && Number(form.court_x) === Number(selected.court_x.toFixed(2)) && Number(form.court_y) === Number(selected.court_y.toFixed(2))} onChange={(x, y) => { setForm((current) => current ? { ...current, court_x: x, court_y: y } : current); setNotice(''); setError('') }} />
          </div>}
          {notice && <p className="success-note" role="status">{notice}</p>}{error && <p className="form-error" role="alert">{error}</p>}<div className="actions"><button className="button primary" disabled={busy}>{busy ? '저장 중…' : adding ? '슛 추가' : '확인하고 저장'}</button>{adding && <button type="button" className="button secondary" onClick={() => { setAdding(false); setSelectedId(data?.shots[0]?.id ?? null) }}>취소</button>}{selected && !adding && <button type="button" className="button danger" onClick={remove} disabled={busy}>기록 삭제</button>}</div>
        </form>}
      </section>
    </div>
  </div>
}
