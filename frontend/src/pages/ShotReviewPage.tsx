import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ShotHeatmap } from '../components/ShotHeatmap'

const API_BASE = 'http://127.0.0.1:8000/api'
const SHOT_TYPES = [
  ['catch_and_shoot', '캐치 앤 슛'], ['set_shot', '제자리 슛'],
  ['dribble_pull_up', '드리블 풀업'], ['layup', '레이업'],
  ['fadeaway', '페이더웨이'], ['step_back', '스텝백'],
  ['turnaround', '턴어라운드'], ['hook_shot', '훅슛'], ['other', '기타'],
]
const TYPE_LABEL = Object.fromEntries(SHOT_TYPES)
const RESULT_LABEL: Record<string, string> = { made: '성공', missed: '실패', needs_review: '확인 필요' }

type Analysis = { id: string; title: string; duration_ms: number; fps: number | null }
type Player = { id: string; display_name: string; jersey_number: string | null }
type Shot = { id: string; player_id: string; player_name: string; candidate_time_ms: number; sequence_start_ms: number; sequence_end_ms: number; court_x: number | null; court_y: number | null; zone_code: string | null; zone_label: string | null; result: string; shot_type: string; status: string; review_reason: string | null; source: string }
type StatLine = { attempts: number; made: number; missed: number; success_rate: number | null; attempt_share: number | null; difference_from_overall: number | null }
type Stats = { overall: StatLine; by_player: Record<string, StatLine>; by_shot_type: Record<string, StatLine>; by_zone: Record<string, StatLine> }
type ShotList = { analysis_status: string; duration_ms: number; fps: number | null; shots: Shot[]; statistics: Stats }
type FormState = { player_id: string; candidate_time_ms: string; sequence_start_ms: string; sequence_end_ms: string; court_x: string; court_y: string; result: string; shot_type: string }

function milliseconds(value: string) { return Math.round(Number(value) * 1000) }
function seconds(value: number) { return (value / 1000).toFixed(2) }
function readableReason(reason: string | null) {
  if (!reason) return '자동 판정을 확인해 주세요.'
  if (reason.includes('OpenAI')) return '외부 AI 판정을 완료하지 못해 사람이 확인해야 합니다.'
  if (reason.includes('court') || reason.includes('좌표')) return '코트 위치를 확정하지 못했습니다.'
  return reason
}
function shotToForm(shot: Shot): FormState {
  return { player_id: shot.player_id, candidate_time_ms: seconds(shot.candidate_time_ms), sequence_start_ms: seconds(shot.sequence_start_ms), sequence_end_ms: seconds(shot.sequence_end_ms), court_x: shot.court_x?.toFixed(2) ?? '', court_y: shot.court_y?.toFixed(2) ?? '', result: shot.result === 'needs_review' ? 'made' : shot.result, shot_type: shot.shot_type === 'needs_review' ? 'other' : shot.shot_type }
}

export function ShotReviewPage() {
  const { analysisId = '' } = useParams()
  const videoRef = useRef<HTMLVideoElement>(null)
  const sequenceEnd = useRef<number | null>(null)
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [players, setPlayers] = useState<Player[]>([])
  const [data, setData] = useState<ShotList | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState<FormState | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [playerFilter, setPlayerFilter] = useState('')
  const [resultFilter, setResultFilter] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [selectedZone, setSelectedZone] = useState<string | null>(null)
  const selected = data?.shots.find((shot) => shot.id === selectedId) ?? null

  const load = useCallback(async () => {
    const query = new URLSearchParams()
    if (playerFilter) query.set('player_id', playerFilter)
    if (resultFilter) query.set('result', resultFilter)
    if (typeFilter) query.set('shot_type', typeFilter)
    const shotUrl = `${API_BASE}/analyses/${analysisId}/shots${query.size ? `?${query}` : ''}`
    const [analysisResponse, playerResponse, shotResponse] = await Promise.all([fetch(`${API_BASE}/analyses/${analysisId}`), fetch(`${API_BASE}/analyses/${analysisId}/players`), fetch(shotUrl)])
    const analysisPayload = await analysisResponse.json(); const playerPayload = await playerResponse.json(); const shotPayload = await shotResponse.json()
    if (!analysisResponse.ok || !playerResponse.ok || !shotResponse.ok) throw new Error(shotPayload.detail?.message ?? '슛 기록을 불러오지 못했습니다.')
    setAnalysis(analysisPayload); setPlayers(playerPayload.players); setData(shotPayload)
    setSelectedId((current) => current && shotPayload.shots.some((shot: Shot) => shot.id === current) ? current : shotPayload.shots[0]?.id ?? null)
  }, [analysisId, playerFilter, resultFilter, typeFilter])

  useEffect(() => { load().catch((caught) => setError(caught.message)) }, [load])
  useEffect(() => { setSelectedZone(null) }, [playerFilter, resultFilter, typeFilter])
  useEffect(() => { if (selected && !adding) setForm(shotToForm(selected)) }, [selected, adding])

  function choose(shot: Shot) {
    setAdding(false); setSelectedId(shot.id); setForm(shotToForm(shot)); setError('')
    sequenceEnd.current = shot.sequence_end_ms / 1000
    if (videoRef.current) videoRef.current.currentTime = shot.sequence_start_ms / 1000
  }
  function startAdding() {
    const duration = analysis?.duration_ms ?? data?.duration_ms ?? 10_000
    const candidate = selected?.candidate_time_ms ?? Math.max(0, Math.round(duration / 2))
    const start = Math.max(0, candidate - 2000); const end = Math.min(duration, Math.max(candidate + 2000, start + 1))
    setAdding(true); setSelectedId(null); setError('')
    setForm({ player_id: players[0]?.id ?? '', candidate_time_ms: seconds(candidate), sequence_start_ms: seconds(start), sequence_end_ms: seconds(end), court_x: '25', court_y: '15', result: 'made', shot_type: 'set_shot' })
  }
  function playSequence() {
    if (!videoRef.current || !form) return
    videoRef.current.currentTime = Number(form.sequence_start_ms); sequenceEnd.current = Number(form.sequence_end_ms)
    videoRef.current.play().catch(() => setError('영상 재생을 시작하지 못했습니다.'))
  }
  function onVideoTime() {
    if (videoRef.current && sequenceEnd.current !== null && videoRef.current.currentTime >= sequenceEnd.current) { videoRef.current.pause(); sequenceEnd.current = null }
  }
  function change(field: keyof FormState, value: string) { setForm((current) => current ? { ...current, [field]: value } : current) }

  async function save(event: FormEvent) {
    event.preventDefault(); if (!form) return
    setBusy(true); setError('')
    try {
      const payload = { player_id: form.player_id, candidate_time_ms: milliseconds(form.candidate_time_ms), sequence_start_ms: milliseconds(form.sequence_start_ms), sequence_end_ms: milliseconds(form.sequence_end_ms), court_x: Number(form.court_x), court_y: Number(form.court_y), result: form.result, shot_type: form.shot_type }
      const url = adding ? `${API_BASE}/analyses/${analysisId}/shots` : `${API_BASE}/analyses/${analysisId}/shots/${selectedId}`
      const response = await fetch(url, { method: adding ? 'POST' : 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      const result = await response.json(); if (!response.ok) throw new Error(result.detail?.message ?? '슛 기록을 저장하지 못했습니다.')
      setAdding(false); setSelectedId(result.id); await load()
    } catch (caught) { setError(caught instanceof Error ? caught.message : '슛 기록을 저장하지 못했습니다.') } finally { setBusy(false) }
  }
  async function remove() {
    if (!selected || !window.confirm(`${seconds(selected.candidate_time_ms)}초 슛 기록을 삭제할까요?`)) return
    setBusy(true); setError('')
    try {
      const response = await fetch(`${API_BASE}/analyses/${analysisId}/shots/${selected.id}`, { method: 'DELETE' })
      if (!response.ok) { const result = await response.json(); throw new Error(result.detail?.message ?? '삭제하지 못했습니다.') }
      setSelectedId(null); setForm(null); await load()
    } catch (caught) { setError(caught instanceof Error ? caught.message : '삭제하지 못했습니다.') } finally { setBusy(false) }
  }

  const stats = data?.statistics.overall
  const reviewCount = data?.shots.filter((shot) => shot.status === 'needs_review').length ?? 0
  const visibleShots = selectedZone ? data?.shots.filter((shot) => shot.zone_code === selectedZone) ?? [] : data?.shots ?? []
  const selectedZoneLabel = visibleShots[0]?.zone_label ?? (selectedZone ? '선택한 구역' : null)
  function selectZone(zone: string) {
    const nextZone = selectedZone === zone ? null : zone
    setSelectedZone(nextZone)
    if (!nextZone) return
    const first = data?.shots.find((shot) => shot.zone_code === nextZone)
    if (first) choose(first)
  }
  return <div className="page shot-review-page">
    <header className="setup-header"><div><p className="eyebrow">PHASE 10 · RESULTS & HEATMAP</p><h1>{analysis?.title ?? '슛 분석 결과'}</h1><p className="lead">요약과 14개 구역 히트맵을 보고, 필요한 기록은 원본 장면에서 바로 확인합니다.</p></div><Link className="button secondary" to={`/progress/${analysisId}`}>분석 화면</Link></header>
    <section className="review-summary" aria-label="결과 요약"><div><span>전체 후보</span><strong>{stats?.attempts ?? 0}</strong></div><div><span>성공 / 실패</span><strong>{stats?.made ?? 0} / {stats?.missed ?? 0}</strong></div><div><span>확인 필요</span><strong>{reviewCount}</strong></div><div><span>확정 성공률</span><strong>{stats?.success_rate == null ? '-' : `${stats.success_rate.toFixed(1)}%`}</strong></div></section>
    <section className="setup-card results-dashboard">
      <div className="panel-title"><div><h2>14개 구역 히트맵</h2><p>색상뿐 아니라 성공/시도와 성공률을 함께 표시합니다. 구역을 누르면 아래 슛 기록도 같이 좁혀집니다.</p></div></div>
      <div className="result-filters" aria-label="결과 필터">
        <label>선수<select value={playerFilter} onChange={(event) => setPlayerFilter(event.target.value)}><option value="">전체 선수</option>{players.map((player) => <option key={player.id} value={player.id}>{player.display_name}{player.jersey_number ? ` #${player.jersey_number}` : ''}</option>)}</select></label>
        <label>성공 여부<select value={resultFilter} onChange={(event) => setResultFilter(event.target.value)}><option value="">전체 결과</option><option value="made">성공</option><option value="missed">실패</option><option value="needs_review">판단 불가</option></select></label>
        <label>슛 종류<select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}><option value="">전체 종류</option>{SHOT_TYPES.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
        <button className="button secondary" type="button" onClick={() => { setPlayerFilter(''); setResultFilter(''); setTypeFilter(''); setSelectedZone(null) }}>필터 초기화</button>
      </div>
      <div className="results-visual-layout">
        <ShotHeatmap statistics={data?.statistics.by_zone ?? {}} overallRate={stats?.success_rate ?? null} selectedZone={selectedZone} onSelect={selectZone} />
        <div className="stat-breakdowns">
          <section><h3>선수별</h3>{Object.entries(data?.statistics.by_player ?? {}).length ? <ul>{Object.entries(data?.statistics.by_player ?? {}).map(([id, line]) => <li key={id}><span>{players.find((player) => player.id === id)?.display_name ?? '선수'}</span><strong>{line.made}/{line.attempts} · {line.success_rate == null ? '-' : `${line.success_rate.toFixed(1)}%`}</strong></li>)}</ul> : <p>기록 없음</p>}</section>
          <section><h3>슛 종류별</h3>{Object.entries(data?.statistics.by_shot_type ?? {}).length ? <ul>{Object.entries(data?.statistics.by_shot_type ?? {}).map(([type, line]) => <li key={type}><span>{TYPE_LABEL[type] ?? (type === 'needs_review' ? '확인 필요' : type)}</span><strong>{line.made}/{line.attempts} · {line.success_rate == null ? '-' : `${line.success_rate.toFixed(1)}%`}</strong></li>)}</ul> : <p>기록 없음</p>}</section>
        </div>
      </div>
    </section>
    <div className="review-layout">
      <section className="setup-card shot-list-panel"><div className="panel-title"><div><h2>{selectedZoneLabel ? `${selectedZoneLabel} 슛 기록` : '슛 기록'}</h2><p>{selectedZone ? `${visibleShots.length}개의 구역 기록을 표시합니다.` : data?.analysis_status === 'completed' ? '모든 기록 확인 완료' : '확인이 필요한 기록이 있습니다.'}</p></div><button className="button primary" onClick={startAdding} disabled={!players.length}>빠진 슛 추가</button></div>
        {!visibleShots.length && <p className="inline-warning">현재 조건에 맞는 슛이 없습니다.{!selectedZone && ' 빠진 슛을 직접 추가할 수 있습니다.'}</p>}
        <ol className="review-shot-list">{visibleShots.map((shot, index) => <li key={shot.id}><button className={selected?.id === shot.id ? 'selected' : ''} onClick={() => choose(shot)}><span className={`result-mark ${shot.result}`}>{shot.result === 'made' ? 'O' : shot.result === 'missed' ? 'X' : '?'}</span><span><strong>{index + 1}. {seconds(shot.candidate_time_ms)}초 · {shot.player_name}</strong><small>{RESULT_LABEL[shot.result]} · {TYPE_LABEL[shot.shot_type] ?? shot.shot_type} · {shot.zone_label ?? '위치 확인 필요'}</small></span><em>{shot.status === 'needs_review' ? '확인 필요' : shot.status === 'user_confirmed' ? '사용자 확인' : '자동 판정'}</em></button></li>)}</ol>
      </section>
      <section className="setup-card shot-detail-panel">
        <video ref={videoRef} src={`${API_BASE}/analyses/${analysisId}/video`} controls preload="metadata" onTimeUpdate={onVideoTime} />
        <div className="video-review-toolbar"><span>원본 {data?.fps ? `${data.fps.toFixed(2)}fps` : 'fps 확인 불가'} · 인위적인 60fps 변환 없음</span><button className="button secondary" onClick={playSequence} disabled={!form}>저장 구간 재생</button></div>
        {selected?.status === 'needs_review' && !adding && <p className="inline-warning"><strong>확인할 이유:</strong> {readableReason(selected.review_reason)}</p>}
        {!form && <p className="helper-text">왼쪽에서 슛을 선택하거나 빠진 슛을 추가해 주세요.</p>}
        {form && <form className="shot-edit-form" onSubmit={save}><h2>{adding ? '빠진 슛 추가' : '선택한 슛 확인·수정'}</h2>
          <div className="shot-form-grid"><label>선수<select value={form.player_id} onChange={(e) => change('player_id', e.target.value)}>{players.map((player) => <option value={player.id} key={player.id}>{player.display_name}{player.jersey_number ? ` #${player.jersey_number}` : ''}</option>)}</select></label><label>슛 시간(초)<input type="number" min="0" max={(data?.duration_ms ?? 0) / 1000} step="0.01" value={form.candidate_time_ms} onChange={(e) => change('candidate_time_ms', e.target.value)} required /></label><label>장면 시작(초)<input type="number" min="0" step="0.01" value={form.sequence_start_ms} onChange={(e) => change('sequence_start_ms', e.target.value)} required /></label><label>장면 끝(초)<input type="number" min="0" max={(data?.duration_ms ?? 0) / 1000} step="0.01" value={form.sequence_end_ms} onChange={(e) => change('sequence_end_ms', e.target.value)} required /></label><label>코트 가로 x (0~50ft)<input type="number" min="0" max="50" step="0.01" value={form.court_x} onChange={(e) => change('court_x', e.target.value)} required /></label><label>코트 세로 y (0~47ft)<input type="number" min="0" max="47" step="0.01" value={form.court_y} onChange={(e) => change('court_y', e.target.value)} required /></label><label>결과<select value={form.result} onChange={(e) => change('result', e.target.value)}><option value="made">성공</option><option value="missed">실패</option></select></label><label>슛 종류<select value={form.shot_type} onChange={(e) => change('shot_type', e.target.value)}>{SHOT_TYPES.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label></div>
          {error && <p className="form-error" role="alert">{error}</p>}<div className="actions"><button className="button primary" disabled={busy}>{busy ? '저장 중…' : adding ? '슛 추가' : '확인하고 저장'}</button>{adding && <button type="button" className="button secondary" onClick={() => { setAdding(false); setSelectedId(data?.shots[0]?.id ?? null) }}>취소</button>}{selected && !adding && <button type="button" className="button danger" onClick={remove} disabled={busy}>기록 삭제</button>}</div>
        </form>}
      </section>
    </div>
  </div>
}
