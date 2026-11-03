import { COURT_ZONES as ZONES, THREE_POINT_LINE, classifyCourtZone } from '../utils/courtZones'

type ZoneStat = {
  attempts: number
  made: number
  missed: number
  success_rate: number | null
  attempt_share: number | null
  difference_from_overall: number | null
}


function zoneTarget(code: string): number {
  if (code === 'restricted') return 80
  if (code.startsWith('paint_')) return 60
  if (code.startsWith('mid_')) return 45
  return 35
}

function zoneTone(stat: ZoneStat | undefined, code: string) {
  if (!stat?.attempts) return 'empty'
  if (stat.success_rate == null) return 'neutral'
  return stat.success_rate >= zoneTarget(code) ? 'hot' : 'cold'
}

function percent(value: number | null | undefined) {
  return value == null ? '-' : `${value.toFixed(1)}%`
}

export function ShotHeatmap({
  statistics,
  selectedZone,
  onSelect,
  patternId = 'courtWood',
}: {
  statistics: Record<string, ZoneStat>
  selectedZone: string | null
  onSelect: (zone: string) => void
  patternId?: string
}) {
  const selected = ZONES.find((zone) => zone.code === selectedZone)
  const selectedStat = selectedZone ? statistics[selectedZone] : undefined
  return <div className="heatmap-wrap">
    <div className="heatmap-heading"><strong>구역별 슈팅 효율</strong><span>목표: 개인 연습용 기준 · DST: 전체 슛 중 해당 구역 비중</span></div>
    <svg className="shot-heatmap" viewBox="-12 -12 524 494" role="img" aria-label="14개 구역 슈팅 히트맵">
      <defs>
        <pattern id={patternId} width="18" height="470" patternUnits="userSpaceOnUse">
          <rect width="18" height="470" fill="#d5b77d" />
          <path d="M1 0V470M17 0V470" stroke="#efd7a9" strokeWidth="1" opacity=".48" />
          <path d="M5 0V470M12 0V470" stroke="#a88757" strokeWidth=".55" opacity=".25" />
        </pattern>
      </defs>
      <rect className="court-surface" x="0" y="0" width="500" height="470" rx="8" fill={`url(#${patternId})`} />
      {ZONES.map((zone) => {
        const stat = statistics[zone.code]
        const rate = stat?.success_rate
        const selectedNow = selectedZone === zone.code
        const target = zoneTarget(zone.code)
        const detail = rate == null ? '데이터 없음' : `${rate.toFixed(1)}% · 목표 ${target}% · 목표 대비 ${(rate - target).toFixed(1)}%p`
        return <g
          className={`heat-zone ${zoneTone(stat, zone.code)}${selectedNow ? ' selected' : ''}`}
          key={zone.code}
          role="button"
          tabIndex={0}
          aria-label={`${zone.label}, ${stat?.made ?? 0}/${stat?.attempts ?? 0}, ${detail}`}
          onClick={(event) => {
            const svg = event.currentTarget.ownerSVGElement
            const matrix = svg?.getScreenCTM()
            if (!matrix) return
            const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse())
            const code = classifyCourtZone(point.x / 10, point.y / 10)
            if (code) onSelect(code)
          }}
          onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(zone.code) } }}
        >
          <path d={zone.path}><title>{`${zone.label} · ${stat?.made ?? 0}/${stat?.attempts ?? 0} · ${detail}`}</title></path>
        </g>
      })}
      <g className="court-lines" aria-hidden="true">
        <rect x="1" y="1" width="498" height="468" rx="8" />
        <path d={THREE_POINT_LINE} />
        <rect x="170" y="0" width="160" height="190" />
        <path d="M210 52.5A40 40 0 0 0 290 52.5" />
        <circle cx="250" cy="52.5" r="7" />
        <path d="M228 34H272" />
        <path d="M205 190A45 45 0 0 0 295 190" />
        <path d="M190 470A60 60 0 0 1 310 470" />
      </g>
      {selected && <path className="selected-zone-outline" d={selected.path} aria-hidden="true" />}
      {ZONES.map((zone) => {
        const stat = statistics[zone.code]
        const width = zone.cardWidth ?? 82
        return <g
          className="zone-stat-card"
          key={`${zone.code}-statistics`}
          transform={`translate(${zone.cardX - width / 2} ${zone.cardY - 34})`}
          onClick={() => onSelect(zone.code)}
        >
          <rect width={width} height="68" rx="5" />
          <text className="zone-fraction" x={width / 2} y="19">{stat?.made ?? 0}/{stat?.attempts ?? 0}</text>
          <text x={width / 2} y="34">{percent(stat?.success_rate)}</text>
          <text x={width / 2} y="48">목표 {zoneTarget(zone.code)}%</text>
          <text x={width / 2} y="61">DST {percent(stat?.attempt_share ?? 0)}</text>
        </g>
      })}
    </svg>
    {selected && <div className="selected-zone-detail" aria-live="polite">
      <strong>{selected.label}</strong><span>성공/시도 {selectedStat?.made ?? 0}/{selectedStat?.attempts ?? 0}</span><span>성공률 {percent(selectedStat?.success_rate)}</span><span>슛 비중 {percent(selectedStat?.attempt_share ?? 0)}</span><span>개인 목표 {zoneTarget(selected.code)}%</span>
    </div>}
    <div className="heatmap-legend" aria-label="히트맵 색상 설명">
      <span><i className="hot" />목표 이상</span><span><i className="cold" />목표 미만</span><span><i className="empty" />시도 없음</span>
    </div>
  </div>
}
