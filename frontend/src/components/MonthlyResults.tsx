import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import type { CourtShot } from './ShotCourt3D'
import { ShotHeatmap } from './ShotHeatmap'
import './MonthlyResults.css'
import { ShotResultSummary } from './ShotResultSummary'
import { shotCategory, shotNumbers, formatShotTime, SHOT_CATEGORY_LABELS } from '../utils/shotPresentation'

const ShotCourt3D = lazy(() => import('./ShotCourt3D').then((module) => ({ default: module.ShotCourt3D })))
const TYPE_LABELS: Record<string, string> = {
  catch_and_shoot: '캐치 앤 슛', set_shot: '제자리 슛', dribble_pull_up: '드리블 풀업', side_step: '사이드 스텝',
  layup: '레이업', dunk: '덩크', fadeaway: '페이더웨이', step_back: '스텝백',
  turnaround: '턴어라운드', hook_shot: '훅슛', other: '기타', needs_review: '확인 필요',
}

type StatLine = { attempts: number; made: number; missed: number; success_rate: number | null; attempt_share: number | null; difference_from_overall: number | null }
type MonthlyShot = CourtShot & { analysis_id: string; analysis_title: string; jersey_number: string | null; zone_code: string | null }
type MonthlyData = {
  month: string | null
  available_months: string[]
  analysis_count: number
  unresolved_count: number
  statistics: { overall: StatLine; by_zone: Record<string, StatLine> }
  shots: MonthlyShot[]
}

export function MonthlyResults() {
  const navigate = useNavigate()
  const [month, setMonth] = useState('')
  const [data, setData] = useState<MonthlyData | null>(null)
  const [selectedZone, setSelectedZone] = useState<string | null>(null)
  const courtRef = useRef<HTMLDivElement>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    const query = month ? `?month=${encodeURIComponent(month)}` : ''
    fetch(`http://127.0.0.1:8000/api/analyses/monthly${query}`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error('월별 결과를 불러오지 못했습니다.')
        return response.json() as Promise<MonthlyData>
      })
      .then((payload) => { setData(payload); setSelectedId(null); setError('') })
      .catch((caught) => { if (caught.name !== 'AbortError') setError(caught.message) })
    return () => controller.abort()
  }, [month])

  const numbers = useMemo(() => shotNumbers(data?.shots ?? []), [data?.shots])
  const overall = data?.statistics.overall
  const visibleShots = data?.shots.filter((shot) => !selectedZone || shot.zone_code === selectedZone) ?? []
  const selected = visibleShots.find((shot) => shot.id === selectedId) ?? null
  return <div className="monthly-results" aria-label="월별 분석 결과">
    <section className="setup-card">
      <div className="panel-title monthly-results-heading">
        <div><h2>월별 분석 결과</h2><p>저장된 슛을 한 달 단위로 모아 보여줍니다.</p></div>
        <label>조회 월<select value={month || data?.month || ''} onChange={(event) => { setMonth(event.target.value); setSelectedZone(null) }} disabled={!data?.available_months.length}>
          {!data?.available_months.length && <option value="">기록 없음</option>}
          {data?.available_months.map((value) => <option key={value} value={value}>{value}</option>)}
        </select></label>
      </div>
      {error && <p className="form-error" role="alert">{error}</p>}
      {!data && !error && <p role="status">월별 결과를 불러오는 중입니다…</p>}
      {data && <>
        <p>분석 기록 {data.analysis_count}개</p>
        <ShotResultSummary shots={data.shots} />
        {overall?.attempts ? <ShotHeatmap
          statistics={data.statistics.by_zone}
          selectedZone={selectedZone}
          onSelect={(zone) => { setSelectedZone((current) => current === zone ? null : zone); setSelectedId(null); requestAnimationFrame(() => courtRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })) }}
          patternId="monthlyCourtWood"
        /> : <p className="inline-warning">이 달에 저장된 슛 기록이 없습니다.</p>}
        <p className="monthly-results-note">개인 연습용 목표는 골밑 80%, 페인트존 60%, 미드레인지 45%, 3점 35%입니다. 성공·실패가 정해진 슛이 1개라도 있으면 구역 성공률을 목표와 비교합니다. 목표 이상은 초록, 미만은 빨강입니다. 이 목표는 리그 평균이 아닙니다.</p>
      </>}
    </section>
    {data && <>
      <div ref={courtRef} style={{ scrollMarginTop: 24 }}><Suspense fallback={<p className="inline-warning">3D 코트를 불러오는 중입니다…</p>}><ShotCourt3D
        shots={visibleShots}
        numbers={numbers}
        selectedShot={selected}
        jerseyNumber={selected?.jersey_number ?? null}
        onSelect={(id) => { const shot = visibleShots.find((item) => item.id === id); if (shot) navigate(`/results/${shot.analysis_id}?shot=${shot.id}`) }}
        typeLabels={TYPE_LABELS}
        zone={selectedZone}
        onZoneChange={(zone) => { setSelectedZone(zone); setSelectedId(null) }}
      /></Suspense></div>
      <section className="setup-card monthly-shot-list"><h2>{selectedZone ? '선택한 구역의 슛' : '월별 슛 목록'}</h2>
        <p>겹친 번호는 목록에서도 선택할 수 있습니다. 원본 영상은 각 분석 결과에서 확인합니다.</p>
        {visibleShots.length ? <ul>{visibleShots.map((shot) => <li key={shot.id}>
          <button type="button" className={selectedId === shot.id ? 'selected' : ''} onClick={() => setSelectedId(shot.id)}>
            <span className={`result-mark ${shotCategory(shot)}`}>{numbers[shot.id]}</span>
            <span>{numbers[shot.id]}번 · {SHOT_CATEGORY_LABELS[shotCategory(shot)]} · {shot.player_name} · {formatShotTime(shot.candidate_time_ms)} · {shot.zone_label ?? '위치 확인 필요'}</span>
          </button>
          <Link to={`/results/${shot.analysis_id}`}>원본 결과</Link>
        </li>)}</ul> : <p>표시할 슛이 없습니다.</p>}
      </section>
      <p className="monthly-results-note">촬영 날짜가 있으면 촬영 월, 없으면 한국 시간 등록 월로 묶습니다. 같은 영상을 여러 번 분석한 시험 기록도 각각 합산합니다. ‘슛 아님’ 후보는 제외합니다. 기존 일부 기록에는 임시 코트 기준점이 있어 구역별 위치 정확도는 아직 검증되지 않았습니다.</p>
    </>}
  </div>
}
