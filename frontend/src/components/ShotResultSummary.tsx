import { shotSummary } from '../utils/shotPresentation'
import type { PresentableShot } from '../utils/shotPresentation'
import type { ShotCategory } from '../utils/shotPresentation'

export function ShotResultSummary({ shots, selectedCategory = null, onSelect, onOpen }: {
  shots: PresentableShot[]
  selectedCategory?: ShotCategory | null
  onSelect?: (category: ShotCategory | null) => void
  onOpen?: (category: ShotCategory | null) => void
}) {
  const counts = shotSummary(shots)
  const cards: { label: string; count: number; category: ShotCategory | null }[] = [
    { label: '전체 후보', count: counts.total, category: null },
    { label: '성공', count: counts.made, category: 'made' },
    { label: '실패', count: counts.missed, category: 'missed' },
    { label: '위치 확인 필요', count: counts.position_review, category: 'position_review' },
    { label: '결과 확인 필요', count: counts.outcome_review, category: 'outcome_review' },
  ]
  return <section className="review-summary" aria-label="결과 요약">
    {cards.filter(card => card.count > 0 || (card.category !== 'position_review' && card.category !== 'outcome_review')).map(card => onSelect
      ? <button key={card.label} type="button" aria-label={`${card.label} 보기`} aria-pressed={selectedCategory === card.category} onClick={() => onSelect(card.category)} onDoubleClick={() => onOpen?.(card.category)}><span>{card.label}</span><strong>{card.count}</strong></button>
      : <div key={card.label}><span>{card.label}</span><strong>{card.count}</strong></div>)}
  </section>
}
