type ZoneStat = {
  attempts: number
  made: number
  missed: number
  success_rate: number | null
  attempt_share: number | null
  difference_from_overall: number | null
}

type ZoneShape = {
  code: string
  label: string
  path: string
  cardX: number
  cardY: number
  cardWidth?: number
}

const ZONES: ZoneShape[] = [
  { code: 'three_corner_left', label: '왼쪽 코너 3점', path: 'M0 0H30V140H0Z', cardX: 38, cardY: 55, cardWidth: 70 },
  { code: 'three_corner_right', label: '오른쪽 코너 3점', path: 'M470 0H500V140H470Z', cardX: 462, cardY: 55, cardWidth: 70 },
  { code: 'three_wing_left', label: '왼쪽 윙 3점', path: 'M0 140H30C55 215 110 265 200 286L150 470H0Z', cardX: 80, cardY: 370 },
  { code: 'three_center', label: '정면 3점', path: 'M200 286Q250 300 300 286L350 470H150Z', cardX: 250, cardY: 406 },
  { code: 'three_wing_right', label: '오른쪽 윙 3점', path: 'M500 140H470C445 215 390 265 300 286L350 470H500Z', cardX: 420, cardY: 370 },
  { code: 'mid_baseline_left', label: '왼쪽 베이스라인 미드레인지', path: 'M30 0H170V130H90Q48 106 30 70Z', cardX: 130, cardY: 55, cardWidth: 70 },
  { code: 'mid_wing_left', label: '왼쪽 윙 미드레인지', path: 'M30 70V140C55 215 110 265 200 286L170 190V130H90Q48 106 30 70Z', cardX: 112, cardY: 175 },
  { code: 'mid_center', label: '정면 미드레인지', path: 'M170 190H330L300 286Q250 300 200 286Z', cardX: 250, cardY: 250 },
  { code: 'mid_wing_right', label: '오른쪽 윙 미드레인지', path: 'M470 70V140C445 215 390 265 300 286L330 190V130H410Q452 106 470 70Z', cardX: 388, cardY: 175 },
  { code: 'mid_baseline_right', label: '오른쪽 베이스라인 미드레인지', path: 'M330 0H470V70Q452 106 410 130H330Z', cardX: 370, cardY: 55, cardWidth: 70 },
  { code: 'paint_left', label: '페인트존 왼쪽', path: 'M170 0H223V190H170Z', cardX: 195, cardY: 157, cardWidth: 48 },
  { code: 'paint_center', label: '페인트존 중앙', path: 'M223 0H277V190H223Z', cardX: 250, cardY: 157, cardWidth: 48 },
  { code: 'paint_right', label: '페인트존 오른쪽', path: 'M277 0H330V190H277Z', cardX: 305, cardY: 157, cardWidth: 48 },
  { code: 'restricted', label: '골밑', path: 'M210 53A40 40 0 1 0 290 53A40 40 0 1 0 210 53Z', cardX: 250, cardY: 55, cardWidth: 60 },
]

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
          onClick={() => onSelect(zone.code)}
          onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(zone.code) } }}
        >
          <path d={zone.path}><title>{`${zone.label} · ${stat?.made ?? 0}/${stat?.attempts ?? 0} · ${detail}`}</title></path>
        </g>
      })}
      <g className="court-lines" aria-hidden="true">
        <rect x="1" y="1" width="498" height="468" rx="8" />
        <path d="M30 0V140M470 0V140M30 140C55 215 110 265 200 286Q250 300 300 286C390 265 445 215 470 140" />
        <rect x="170" y="0" width="160" height="190" />
        <path d="M210 53A40 40 0 0 0 290 53" />
        <circle cx="250" cy="53" r="7" />
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
    <div className="selected-zone-detail" aria-live="polite">
      {selected ? <><strong>{selected.label}</strong><span>성공/시도 {selectedStat?.made ?? 0}/{selectedStat?.attempts ?? 0}</span><span>성공률 {percent(selectedStat?.success_rate)}</span><span>슛 비중 {percent(selectedStat?.attempt_share ?? 0)}</span><span>개인 목표 {zoneTarget(selected.code)}%</span></> : <span>구역을 선택하면 해당 구역의 슛 목록과 상세 수치를 볼 수 있습니다.</span>}
    </div>
    <div className="heatmap-legend" aria-label="히트맵 색상 설명">
      <span><i className="hot" />목표 이상</span><span><i className="cold" />목표 미만</span><span><i className="neutral" />성공·실패 판단 불가</span><span><i className="empty" />시도 없음</span>
    </div>
  </div>
}
