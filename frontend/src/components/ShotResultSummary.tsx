import { shotSummary } from '../utils/shotPresentation'
import type { PresentableShot } from '../utils/shotPresentation'

export function ShotResultSummary({ shots }: { shots: PresentableShot[] }) {
  const counts = shotSummary(shots)
  return <>
    <section className="review-summary" aria-label="결과 요약">
      <div><span>전체 후보</span><strong>{counts.total}</strong></div>
      <div><span>성공</span><strong>{counts.made}</strong></div>
      <div><span>실패</span><strong>{counts.missed}</strong></div>
      <div><span>위치 확인 필요</span><strong>{counts.position_review}</strong></div>
      <div><span>결과 확인 필요</span><strong>{counts.outcome_review}</strong></div>
    </section>
    <p className="summary-count-note">성공·실패는 위치와 결과가 모두 정해진 기록입니다. 둘 다 미정이면 결과 확인 필요에만 집계하며, 저장 후 해당 항목으로 이동합니다.</p>
  </>
}
