export const SHOT_TYPES = [
  ['catch_and_shoot', '캐치 앤 슛'], ['set_shot', '제자리 슛'],
  ['dribble_pull_up', '드리블 풀업'], ['side_step', '사이드 스텝'], ['layup', '레이업'], ['dunk', '덩크'],
  ['fadeaway', '페이더웨이'], ['step_back', '스텝백'],
  ['turnaround', '턴어라운드'], ['hook_shot', '훅슛'], ['other', '기타'],
] as const

export const SHOT_TYPE_LABELS: Record<string, string> = {
  ...Object.fromEntries(SHOT_TYPES), needs_review: '종류 확인 필요',
}

export function shotTypeStatistics(shots: { shot_type: string; result: string }[]) {
  const types = new Set(shots.map(shot => shot.shot_type))
  return [...types].map(type => {
    const matching = shots.filter(shot => shot.shot_type === type)
    const made = matching.filter(shot => shot.result === 'made').length
    const missed = matching.filter(shot => shot.result === 'missed').length
    return {
      type, attempts: matching.length, made, missed,
      unresolved: matching.length - made - missed,
      successRate: made + missed ? made / (made + missed) * 100 : null,
    }
  }).sort((a, b) => b.attempts - a.attempts || a.type.localeCompare(b.type))
}
