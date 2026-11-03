import type { KeyboardEvent, MouseEvent } from 'react'
import './ShotPositionPicker.css'

export function ShotPositionPicker({ x, y, disabled = false, saved = false, onChange }: {
  x: string
  y: string
  disabled?: boolean
  saved?: boolean
  onChange: (x: string, y: string) => void
}) {
  const hasPosition = x.trim() !== '' && y.trim() !== '' &&
    Number.isFinite(Number(x)) && Number.isFinite(Number(y)) &&
    Number(x) >= 0 && Number(x) <= 50 && Number(y) >= 0 && Number(y) <= 47

  function select(xValue: number, yValue: number) {
    if (!disabled) onChange(Math.max(0, Math.min(50, xValue)).toFixed(2), Math.max(0, Math.min(47, yValue)).toFixed(2))
  }
  function click(event: MouseEvent<SVGSVGElement>) {
    const svg = event.currentTarget
    const transform = svg.getScreenCTM()
    if (!transform || disabled) return
    const point = svg.createSVGPoint()
    point.x = event.clientX
    point.y = event.clientY
    const court = point.matrixTransform(transform.inverse())
    // Ignore the letterboxed space outside the actual court.
    if (court.x < 0 || court.x > 500 || court.y < 0 || court.y > 470) return
    select(court.x / 10, court.y / 10)
  }
  function keyDown(event: KeyboardEvent<SVGSVGElement>) {
    const step = event.shiftKey ? 0.1 : 1
    const moves: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }
    const move = moves[event.key]
    if (!move || disabled) return
    event.preventDefault()
    select((hasPosition ? Number(x) : 25) + move[0], (hasPosition ? Number(y) : 23.5) + move[1])
  }
  return <section className="shot-position-picker" aria-label="슛 위치 직접 지정">
    <h3>2D 코트에서 슛 위치 찍기</h3>
    <svg viewBox="0 0 500 470" role="application" aria-label="슛 위치 선택용 2D 반코트" aria-disabled={disabled} tabIndex={disabled ? -1 : 0} onClick={click} onKeyDown={keyDown}>
      <rect width="500" height="470" fill="#ba8c52" />
      <g className="position-court-lines">
        <rect x="1" y="1" width="498" height="468" />
        <rect x="170" y="0" width="160" height="190" />
        <path d="M30 0V141.98A237.5 237.5 0 0 0 470 141.98V0" />
        <path d="M210 52.5A40 40 0 0 0 290 52.5M190 190A60 60 0 0 0 310 190M190 470A60 60 0 0 1 310 470M220 35H280" />
        <circle cx="250" cy="52.5" r="7" />
      </g>
      <g className="position-court-labels" aria-hidden="true"><text x="250" y="26">골대</text><text x="65" y="440">코트 왼쪽</text><text x="435" y="440">코트 오른쪽</text></g>
      {hasPosition && <g transform={`translate(${Number(x) * 10} ${Number(y) * 10})`} aria-hidden="true">
        <circle r="11" fill="#dfff59" stroke="#102019" strokeWidth="3" />
        <path d="M-16 0H16M0-16V16" stroke="#102019" strokeWidth="2" />
      </g>}
    </svg>
    <p className="position-selection" role="status">{hasPosition ? `${saved ? '저장된 위치' : '선택 위치'}: x ${Number(x).toFixed(2)} · y ${Number(y).toFixed(2)}ft${saved ? '' : ' (저장 전)'}` : '아직 위치를 선택하지 않았습니다.'}</p>
  </section>
}
