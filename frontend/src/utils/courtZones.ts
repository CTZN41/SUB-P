import geometry from './courtGeometry.json' with { type: 'json' }

export type ZoneCode = keyof typeof geometry.zones
export const COURT_RULES = geometry.parameters
export const ZONE_POLYGONS = geometry.zones

// Feet and boundary precedence match backend classify_zone.
export function classifyCourtZone(x: number, y: number): ZoneCode | null {
  const p = COURT_RULES
  if (!Number.isFinite(x) || !Number.isFinite(y) || x < 0 || x > p.COURT_WIDTH_FT || y < 0 || y > p.HALF_COURT_LENGTH_FT) return null
  const dx = x - p.HOOP_X_FT
  const distance = Math.hypot(dx, y - p.HOOP_Y_FT)
  if (y <= p.CORNER_THREE_END_Y_FT && x <= p.CORNER_THREE_X_FT) return 'three_corner_left'
  if (y <= p.CORNER_THREE_END_Y_FT && x >= p.COURT_WIDTH_FT - p.CORNER_THREE_X_FT) return 'three_corner_right'
  if (distance >= p.THREE_POINT_RADIUS_FT - p.BOUNDARY_EPSILON_FT) return dx < -p.THREE_CENTER_HALF_WIDTH_FT ? 'three_wing_left' : dx > p.THREE_CENTER_HALF_WIDTH_FT ? 'three_wing_right' : 'three_center'
  if (distance <= p.RESTRICTED_RADIUS_FT + p.BOUNDARY_EPSILON_FT) return 'restricted'
  if (x >= p.PAINT_LEFT_X_FT && x <= p.PAINT_RIGHT_X_FT && y <= p.FREE_THROW_LINE_Y_FT) {
    const third = (p.PAINT_RIGHT_X_FT - p.PAINT_LEFT_X_FT) / 3
    return x <= p.PAINT_LEFT_X_FT + third ? 'paint_left' : x <= p.PAINT_RIGHT_X_FT - third ? 'paint_center' : 'paint_right'
  }
  if (y <= p.BASELINE_MID_END_Y_FT && x < p.PAINT_LEFT_X_FT) return 'mid_baseline_left'
  if (y <= p.BASELINE_MID_END_Y_FT && x > p.PAINT_RIGHT_X_FT) return 'mid_baseline_right'
  return dx < -p.MID_CENTER_HALF_WIDTH_FT ? 'mid_wing_left' : dx > p.MID_CENTER_HALF_WIDTH_FT ? 'mid_wing_right' : 'mid_center'
}

export type ZoneShape = {
  code: ZoneCode
  label: string
  path: string
  cardX: number
  cardY: number
  cardWidth?: number
}

export function zonePath(code: ZoneCode): string {
  return ZONE_POLYGONS[code].flatMap(polygon => polygon.map(ring =>
    `M${ring.map(([x, y]) => `${x * 10} ${y * 10}`).join('L')}Z`,
  )).join(' ')
}

const LABELS: Omit<ZoneShape, 'path'>[] = [
  { code: 'three_corner_left', label: '왼쪽 코너 3점', cardX: 38, cardY: 55, cardWidth: 70 },
  { code: 'three_corner_right', label: '오른쪽 코너 3점', cardX: 462, cardY: 55, cardWidth: 70 },
  { code: 'three_wing_left', label: '왼쪽 윙 3점', cardX: 80, cardY: 370 },
  { code: 'three_center', label: '정면 3점', cardX: 250, cardY: 406 },
  { code: 'three_wing_right', label: '오른쪽 윙 3점', cardX: 420, cardY: 370 },
  { code: 'mid_baseline_left', label: '왼쪽 베이스라인 미드레인지', cardX: 130, cardY: 55, cardWidth: 70 },
  { code: 'mid_wing_left', label: '왼쪽 윙 미드레인지', cardX: 112, cardY: 175 },
  { code: 'mid_center', label: '정면 미드레인지', cardX: 250, cardY: 250 },
  { code: 'mid_wing_right', label: '오른쪽 윙 미드레인지', cardX: 388, cardY: 175 },
  { code: 'mid_baseline_right', label: '오른쪽 베이스라인 미드레인지', cardX: 370, cardY: 55, cardWidth: 70 },
  { code: 'paint_left', label: '페인트존 왼쪽', cardX: 196.6667, cardY: 157, cardWidth: 48 },
  { code: 'paint_center', label: '페인트존 중앙', cardX: 250, cardY: 157, cardWidth: 48 },
  { code: 'paint_right', label: '페인트존 오른쪽', cardX: 303.3333, cardY: 157, cardWidth: 48 },
  { code: 'restricted', label: '골밑', cardX: 250, cardY: 55, cardWidth: 60 },
]

export const COURT_ZONES: ZoneShape[] = LABELS.map(zone => ({ ...zone, path: zonePath(zone.code) }))

const p = COURT_RULES
const cornerJoin = p.HOOP_Y_FT + Math.sqrt(p.THREE_POINT_RADIUS_FT ** 2 - (p.HOOP_X_FT - p.CORNER_THREE_X_FT) ** 2)
export const THREE_POINT_LINE = `M${p.CORNER_THREE_X_FT * 10} 0V${cornerJoin * 10}A${p.THREE_POINT_RADIUS_FT * 10} ${p.THREE_POINT_RADIUS_FT * 10} 0 0 0 ${(p.COURT_WIDTH_FT - p.CORNER_THREE_X_FT) * 10} ${cornerJoin * 10}V0`
