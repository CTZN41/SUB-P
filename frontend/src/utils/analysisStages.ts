export const ANALYSIS_STAGES = ['영상 준비', '기준 확인', '선수·공 추적', '후보 찾기', '후보 판단', '심층 분석', '결과 계산', '완료']

const ACTIVITY_LABELS: Record<string, string> = {
  '슛 여부 확인·추가 재탐지': '추가 후보 탐지',
  '카메라 구도 확인': '위치 재검토',
  '슛 여부 확인': '슛 여부 검증',
}

// Keep compatibility with the existing worker and saved jobs.
export function analysisStageIndex(stage?: string | null): number {
  if (!stage) return 0
  const base = stage.split(' ·')[0].replace(/\s+\d+\s*\/\s*\d+\s*$/, '')
  if (base === '성공·실패와 슛 종류 판단') {
    if (stage.includes('슛 여부') || stage.includes('추가 재탐지')) return 4
    if (stage.includes('상세 AI') || stage.includes('카메라 구도')) return 5
    return 6
  }
  if (base === '코트와 골대 확인') return 1
  if (base === '슛 후보 찾기') return 3
  return ANALYSIS_STAGES.indexOf(base)
}

export function analysisStageLabel(stage?: string | null): string {
  if (!stage) return '시작 전'
  const index = analysisStageIndex(stage)
  if (index < 0) return stage
  const progress = (index === 4 || index === 5) ? stage.match(/\b(\d+)\s*\/\s*(\d+)\b/) : null
  const label = ANALYSIS_STAGES[index] + (progress ? ` ${progress[1]}/${progress[2]}` : '')
  if (index !== 4 && index !== 5) return label
  const parts = stage.split(' · ').slice(1)
  const detail = parts.map(part => part.replace(/\b\d+\s*\/\s*\d+\b/g, '').trim())
    .map(part => ACTIVITY_LABELS[part] ?? part)
    .filter(part => part && !/^(슛 여부 확인|상세 AI 판정)$/.test(part)).join(' · ')
  const completed = progress && progress[1] === progress[2] && Number(progress[2]) > 0
  if (completed && detail === '슛 여부 검증') return `${label} · 후속 검증 중`
  if (detail) return `${label} · ${detail}`
  if (progress && progress[1] === progress[2] && Number(progress[2]) > 0) {
    return `${label} · ${index === 4 ? '후속 검증 중' : '결과 정리 중'}`
  }
  return label
}
