export const ANALYSIS_STAGES = ['영상 준비', '기준 확인', '선수·공 추적', '후보 찾기', '후보 판단', '심층 분석', '결과 계산', '완료']

// Keep compatibility with the existing worker and saved jobs.
export function analysisStageIndex(stage?: string | null): number {
  if (!stage) return 0
  const base = stage.split(' ·')[0]
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
  const separator = stage.indexOf(' ·')
  return ANALYSIS_STAGES[index] + (separator >= 0 ? stage.slice(separator) : '')
}
