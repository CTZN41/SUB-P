export type HeatmapStat = { attempts: number; success_rate: number | null }

export function zoneTarget(code: string): number {
  if (code === 'restricted' || code.startsWith('paint_')) return 90
  return code.startsWith('mid_') ? 50 : 40
}

export function zoneTone(stat: HeatmapStat | undefined, code: string): 'empty' | 'neutral' | 'hot' | 'cold' {
  if (!stat || stat.attempts < 5) return 'empty'
  if (stat.success_rate == null) return 'neutral'
  return stat.success_rate >= zoneTarget(code) ? 'hot' : 'cold'
}
