import { lazy, Suspense, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import type { RecordItem } from './AnalysisHistory'
import type { CourtShot } from './ShotCourt3D'
import { zoneTarget } from './ShotHeatmap'
import { COURT_ZONES, THREE_POINT_LINE } from '../utils/courtZones'
import { hasShotPosition, shotNumbers } from '../utils/shotPresentation'

const ShotCourt3D = lazy(() => import('./ShotCourt3D').then(module => ({ default: module.ShotCourt3D })))
type PreviewData = {
  shots: CourtShot[]
  statistics: { by_zone: Record<string, { attempts: number; success_rate: number | null }> }
}

export function HomeAnalysisPreview({ analysis, loading }: { analysis: RecordItem | null; loading: boolean }) {
  const navigate = useNavigate()
  const [response, setResponse] = useState<{ id: string; data: PreviewData } | null>(null)
  const [failure, setFailure] = useState<{ id: string; message: string } | null>(null)
  const analysisId = analysis?.id

  useEffect(() => {
    if (!analysisId) return
    const controller = new AbortController()
    fetch(`/api/analyses/${analysisId}/shots`, { signal: controller.signal })
      .then(async result => {
        if (!result.ok) throw new Error('미리보기를 불러오지 못했습니다. 결과 화면에서 확인해 주세요.')
        return result.json() as Promise<PreviewData>
      })
      .then(data => setResponse({ id: analysisId, data }))
      .catch(error => { if (error.name !== 'AbortError') setFailure({ id: analysisId, message: error.message }) })
    return () => controller.abort()
  }, [analysisId])

  const data = response?.id === analysisId ? response?.data : undefined
  const error = failure?.id === analysisId ? failure?.message : undefined
  const pending = loading || (!!analysisId && !data && !error)
  const shots = data?.shots ?? []
  const located = shots.filter(hasShotPosition).length
  const resultPath = analysisId ? `/results/${analysisId}` : '/new'

  return <section className="home-preview" aria-label="최근 분석 결과 미리보기" aria-busy={pending}>
    <div className="home-preview-header">
      <div><span className="home-section-kicker">YOUR SHOT MAP</span><h2>{analysis ? '내 최근 분석' : '코트 위에 쌓이는 슛 기록'}</h2></div>
      <Link to={resultPath} className="home-preview-link" aria-label={analysis ? '최근 분석 결과 자세히 보기' : '첫 영상 분석 시작'}>↗</Link>
    </div>
    {analysis && <p className="home-preview-title" title={analysis.title}>{analysis.title}</p>}
    <div className="home-court-preview">
      <Suspense fallback={<div className="home-preview-loading" role="status">슈팅 맵을 준비하고 있습니다…</div>}>
        <ShotCourt3D shots={shots} numbers={shotNumbers(shots)} selectedShot={null} jerseyNumber={null}
          showPlayerName={false} typeLabels={{}} initialCameraPosition={[20, 44, 60]}
          onSelect={id => navigate(`${resultPath}?shot=${encodeURIComponent(id)}`)} />
      </Suspense>
    </div>
    {pending && <p className="home-preview-status" role="status">최근 분석을 불러오고 있습니다…</p>}
    {error && <p className="home-preview-status" role="status">{error}</p>}
    <div className="home-preview-bottom">
      {data ? <>
        <div className="home-preview-stat"><strong>{shots.length}<small>개</small></strong><span>분석한 슛</span></div>
        <div className="home-preview-stat"><strong>{located}<small>개</small></strong><span>위치가 표시된 슛</span></div>
      </> : <div className="home-preview-empty"><strong>내 슛이 한눈에 보이도록</strong><span>영상 분석 후 슈팅 맵을 확인하세요.</span></div>}
      <Link className="home-mini-map" to={resultPath} aria-label={analysis ? '14개 구역 히트맵 결과 보기' : '영상 분석 시작'}>
        <svg viewBox="-5 -5 510 480" aria-hidden="true">
          <rect width="500" height="470" rx="8" fill="#555c57" />
          {COURT_ZONES.map(zone => {
            const stat = data?.statistics.by_zone[zone.code]
            const fill = !stat?.attempts || stat.success_rate == null ? '#555c57' : stat.success_rate >= zoneTarget(zone.code) ? '#257b57' : '#98504f'
            return <path key={zone.code} d={zone.path} fill={fill} stroke="#353e39" strokeWidth="2" />
          })}
          <g fill="none" stroke="#e3e8df" strokeWidth="4">
            <rect x="1" y="1" width="498" height="468" rx="8" />
            <path d={THREE_POINT_LINE} /><rect x="170" y="0" width="160" height="190" />
            <circle cx="250" cy="52.5" r="7" /><path d="M205 190A45 45 0 0 0 295 190" />
          </g>
        </svg>
        <span>14개 구역<br /><strong>히트맵 ↗</strong></span>
      </Link>
    </div>
  </section>
}
