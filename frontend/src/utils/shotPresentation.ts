export type ShotCategory = 'made' | 'missed' | 'position_review' | 'outcome_review'
export type PresentableShot = { id: string; result: string; court_x: number | null; court_y: number | null }

export const SHOT_CATEGORY_LABELS: Record<ShotCategory, string> = {
  made: '성공', missed: '실패', position_review: '위치 확인 필요', outcome_review: '결과 확인 필요',
}

export function hasShotPosition(shot: PresentableShot): boolean {
  return shot.court_x !== null && shot.court_y !== null &&
    Number.isFinite(shot.court_x) && Number.isFinite(shot.court_y) &&
    shot.court_x >= 0 && shot.court_x <= 50 && shot.court_y >= 0 && shot.court_y <= 47
}

// Each shot belongs to exactly one bucket. Unknown outcome takes precedence
// when both outcome and position are missing, so the counts always add up.
export function shotCategory(shot: PresentableShot): ShotCategory {
  if (shot.result !== 'made' && shot.result !== 'missed') return 'outcome_review'
  if (!hasShotPosition(shot)) return 'position_review'
  return shot.result
}

export function shotSummary(shots: PresentableShot[]) {
  const counts = { total: shots.length, made: 0, missed: 0, position_review: 0, outcome_review: 0 }
  for (const shot of shots) counts[shotCategory(shot)]++
  return counts
}

export function shotNumbers(shots: { id: string }[]): Record<string, number> {
  return Object.fromEntries(shots.map((shot, index) => [shot.id, index + 1]))
}

export function formatShotTime(milliseconds: number): string {
  if (!Number.isFinite(milliseconds)) return '--:--'
  const seconds = Math.max(0, Math.floor(milliseconds / 1000))
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
}

export function shouldShowPlayerNames(playType: string, names: string[]): boolean {
  return playType !== 'solo' || names.length > 1 || names.some(name => !/^선수\s+[A-F]$/.test(name.trim()))
}

export function parseShotTime(value: string, originalMilliseconds?: number): number | null {
  if (!/^\d+:[0-5]\d$/.test(value)) return null
  // A location-only edit must not round the original release/segment timing.
  if (originalMilliseconds !== undefined && value === formatShotTime(originalMilliseconds)) return originalMilliseconds
  const [minutes, seconds] = value.split(':').map(Number)
  const milliseconds = (minutes * 60 + seconds) * 1000
  return Number.isSafeInteger(milliseconds) ? milliseconds : null
}
