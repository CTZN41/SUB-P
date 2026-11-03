import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { MonthlyShotPreview } from './MonthlyShotPreview'
import type { CourtShot } from './ShotCourt3D'
import { ShotHeatmap } from './ShotHeatmap'
import './MonthlyResults.css'
import { ShotResultSummary } from './ShotResultSummary'
import { shotCategory, shotNumbers } from '../utils/shotPresentation'
import type { ShotCategory } from '../utils/shotPresentation'

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
  const [month, setMonth] = useState('')
  const [data, setData] = useState<MonthlyData | null>(null)
  const [selectedZone, setSelectedZone] = useState<string | null>(null)
  const [selectedCategory, setSelectedCategory] = useState<ShotCategory | null>(null)
  const courtRef = useRef<HTMLDivElement>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    const query = month ? `?month=${encodeURIComponent(month)}` : ''
    fetch(`/api/analyses/monthly${query}`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error('월별 기록을 불러오지 못했습니다.')
        return response.json() as Promise<MonthlyData>
      })
      .then((payload) => { setData(payload); setSelectedId(null); setError('') })
      .catch((caught) => { if (caught.name !== 'AbortError') setError(caught.message) })
    return () => controller.abort()
  }, [month])

  const numbers = useMemo(() => shotNumbers(data?.shots ?? []), [data?.shots])
  const overall = data?.statistics.overall
  const categoryShots = data?.shots.filter((shot) => !selectedCategory || shotCategory(shot) === selectedCategory) ?? []
  const visibleShots = categoryShots.filter((shot) => !selectedZone || shot.zone_code === selectedZone)
  const zoneStatistics = selectedCategory ? Object.fromEntries(Object.keys(data?.statistics.by_zone ?? {}).map((code) => {
    const shots = categoryShots.filter((shot) => shot.zone_code === code)
    const made = shots.filter((shot) => shotCategory(shot) === 'made').length
    const missed = shots.filter((shot) => shotCategory(shot) === 'missed').length
    return [code, { attempts: shots.length, made, missed, success_rate: made + missed ? made / (made + missed) * 100 : null,
      attempt_share: categoryShots.length ? shots.length / categoryShots.length * 100 : null, difference_from_overall: null }]
  })) : data?.statistics.by_zone ?? {}
  const selected = visibleShots.find((shot) => shot.id === selectedId) ?? null
  return <div className="monthly-results" aria-label="월별 기록">
    <section className="setup-card">
      <div className="panel-title monthly-results-heading">
        <div><h2>결과 확인</h2></div>
        <label>조회 월<select value={month || data?.month || ''} onChange={(event) => { setMonth(event.target.value); setSelectedZone(null) }} disabled={!data?.available_months.length}>
          {!data?.available_months.length && <option value="">기록 없음</option>}
          {data?.available_months.map((value) => <option key={value} value={value}>{value}</option>)}
        </select></label>
      </div>
      {error && <p className="form-error" role="alert">{error}</p>}
      {!data && !error && <p role="status">월별 기록을 불러오는 중입니다…</p>}
      {data && <>
        <p>분석 기록 {data.analysis_count}개</p>
        <ShotResultSummary shots={data.shots} selectedCategory={selectedCategory} onSelect={(category) => { setSelectedCategory(category); setSelectedZone(null); setSelectedId(null) }} />
        {overall?.attempts ? <ShotHeatmap
          statistics={zoneStatistics}
          selectedZone={selectedZone}
          onSelect={(zone) => { setSelectedZone((current) => current === zone ? null : zone); setSelectedId(null); requestAnimationFrame(() => courtRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })) }}
          patternId="monthlyCourtWood"
        /> : <p className="inline-warning">이 달에 저장된 슛 기록이 없습니다.</p>}
      </>}
    </section>
    {data && <>
      <div ref={courtRef} style={{ scrollMarginTop: 24 }}><Suspense fallback={<p className="inline-warning">3D 코트를 불러오는 중입니다…</p>}><ShotCourt3D
        shots={visibleShots}
        numbers={numbers}
        selectedShot={selected}
        selectedAction={selected && <Link className="button secondary" to={`/results/${selected.analysis_id}?shot=${selected.id}`}>원본 분석 보기</Link>}
        showPlayerName={!!selected && !/^선수\s+[A-F]$/.test(selected.player_name.trim())}
        jerseyNumber={selected?.jersey_number ?? null}
        onSelect={setSelectedId}
        typeLabels={TYPE_LABELS}
        zone={selectedZone}
        onZoneChange={(zone) => { setSelectedZone(zone); setSelectedId(null) }}
      /></Suspense></div>
      {selected && <MonthlyShotPreview key={selected.id} shot={selected} number={numbers[selected.id]} typeLabel={TYPE_LABELS[selected.shot_type] ?? selected.shot_type} onClose={() => setSelectedId(null)} />}
    </>}
  </div>
}
