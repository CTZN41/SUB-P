export type ZoneShape = {
  code: string
  label: string
  path: string
  cardX: number
  cardY: number
  cardWidth?: number
}

export const COURT_ZONES: ZoneShape[] = [
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
