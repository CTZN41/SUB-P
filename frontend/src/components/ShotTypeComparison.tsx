import { SHOT_TYPE_LABELS, shotTypeStatistics } from '../utils/shotTypes'
import './ShotPractice.css'

export function ShotTypeComparison({ shots, selectedType, onSelect }: {
  shots: { shot_type: string; result: string }[]
  selectedType: string
  onSelect: (type: string) => void
}) {
  const rows = shotTypeStatistics(shots)
  return <section className="shot-type-comparison" aria-label="슛 종류별 성적 비교">
    <div className="panel-title"><h2>슛 종류별 성적</h2>
      {selectedType && <button type="button" className="button secondary" onClick={() => onSelect('')}>전체 종류</button>}
    </div>
    <p className="helper-text">성공률은 위치 확인 여부와 관계없이 결과가 판정된 슛으로 계산합니다.</p>
    {rows.length ? <div className="shot-type-grid">{rows.map(row =>
      <button type="button" key={row.type} className="shot-type-stat" aria-pressed={selectedType === row.type}
        aria-label={`${SHOT_TYPE_LABELS[row.type] ?? row.type} 성적 보기`}
        onClick={() => onSelect(selectedType === row.type ? '' : row.type)}>
        <strong>{SHOT_TYPE_LABELS[row.type] ?? row.type}</strong>
        <span className="shot-type-rate">{row.successRate == null ? '—' : `${row.successRate.toFixed(1)}%`}</span>
        <span>{row.attempts}회 시도 · 성공 {row.made} / 실패 {row.missed}</span>
        {row.unresolved > 0 && <small>결과 확인 {row.unresolved}개 · 성공률에서 제외</small>}
      </button>)}</div> : <p className="helper-text">현재 조건에 맞는 슛이 없습니다.</p>}
  </section>
}
