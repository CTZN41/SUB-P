import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import './PracticeReport.css'

type Line = { attempts: number; made: number; missed: number; unresolved: number; success_rate: number | null }
type ComparisonRow = { key: string; kind: string; label: string; current: Line; previous: Line | null; attempt_change: number | null; rate_change_pp: number | null }
type Observation = { evidence_key: string; text: string }
type ReportData = {
  current: { id: string; title: string; date: string }
  previous: { id: string; title: string; date: string } | null
  same_source_metadata: boolean; position_unresolved: number; ready: boolean; stale: boolean; ai_available: boolean
  rows: ComparisonRow[]
  report: { generated_at: string; model: string; elapsed_seconds: number; api_usage: { input_tokens: number; output_tokens: number; total_tokens: number } | null
    content: { summary: string; findings: Observation[]; next_practice: Observation[] } } | null
}

function rate(line: Line | null) {
  return line?.success_rate == null ? '—' : `${line.success_rate.toFixed(1)}%`
}

function change(value: number | null, suffix: string) {
  return value == null ? '—' : `${new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 1, signDisplay: 'exceptZero' }).format(value)}${suffix}`
}

async function readResponse(response: Response): Promise<ReportData> {
  const body = await response.json().catch(() => null)
  if (!response.ok) throw new Error(body?.detail?.message ?? '연습 리포트를 불러오지 못했습니다.')
  return body
}

export function PracticeReport({ analysisId, revision }: { analysisId: string; revision: unknown }) {
  const [data, setData] = useState<ReportData | null>(null)
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState('')
  const [kind, setKind] = useState('zone')
  const [retry, setRetry] = useState(0)
  const requestSequence = useRef(0)

  useEffect(() => {
    const controller = new AbortController()
    const sequence = ++requestSequence.current
    setLoading(true)
    setError('')
    fetch(`/api/analyses/${analysisId}/practice-report`, { signal: controller.signal })
      .then(readResponse)
      .then(value => { if (sequence === requestSequence.current) setData(value) })
      .catch(cause => { if (!controller.signal.aborted && sequence === requestSequence.current) setError(cause.message) })
      .finally(() => { if (sequence === requestSequence.current) setLoading(false) })
    return () => { controller.abort(); ++requestSequence.current }
  }, [analysisId, revision, retry])

  async function generate() {
    const sequence = ++requestSequence.current
    setGenerating(true)
    setError('')
    try {
      const response = await fetch(`/api/analyses/${analysisId}/practice-report`, { method: 'POST' })
      const value = await readResponse(response)
      if (sequence === requestSequence.current) setData(value)
    } catch (cause) {
      if (sequence === requestSequence.current) setError(cause instanceof Error ? cause.message : 'AI 요약을 생성하지 못했습니다.')
    } finally {
      setGenerating(false)
    }
  }

  const overall = data?.rows.find(row => row.key === 'overall')
  const rows = data?.rows.filter(row => row.kind === kind) ?? []
  const report = data?.stale ? null : data?.report
  function observation(item: Observation) {
    const evidence = data?.rows.find(row => row.key === item.evidence_key)
    return <li key={`${item.evidence_key}-${item.text}`}><p>{item.text}</p>{evidence &&
      <small>{evidence.label} · 이번 {evidence.current.made}/{evidence.current.made + evidence.current.missed} · {rate(evidence.current)}
        {evidence.previous && evidence.previous.attempts > 0 && ` / 이전 ${evidence.previous.made}/${evidence.previous.made + evidence.previous.missed} · ${rate(evidence.previous)}`}</small>}</li>
  }

  return <section className="setup-card practice-report" aria-label="AI 연습 리포트" aria-busy={loading || generating}>
    <div className="panel-title"><div><p className="eyebrow">PRACTICE REPORT</p><h2>AI 연습 리포트</h2></div>
      {data && <button type="button" className="button primary" disabled={loading || generating || !data.ready || !data.ai_available || Boolean(report)} onClick={generate}>
        {generating ? 'AI 요약 생성 중…' : report ? '요약 생성 완료' : data.stale ? 'AI 요약 갱신' : 'AI 요약 생성'}
      </button>}</div>
    {loading && <p className="helper-text">연습 기록을 비교하고 있습니다…</p>}
    {error && <div role="alert" className="inline-warning">{error} <button type="button" className="button secondary" onClick={() => setRetry(value => value + 1)}>다시 확인</button></div>}
    {!loading && data && overall && <>
      {data.previous && <p className="helper-text">이전 연습 <Link to={`/results/${data.previous.id}`}>{data.previous.title}</Link> · {data.previous.date}</p>}
      {data.same_source_metadata && <p className="helper-text">같은 원본명·길이·용량의 기록입니다. 재분석 결과일 수 있습니다.</p>}
      <div className="practice-overview">
        <div><span>이번 연습</span><strong>{rate(overall.current)}</strong><small>{overall.current.attempts}회 시도 · 성공 {overall.current.made} / 실패 {overall.current.missed}</small></div>
        <div><span>이전 연습</span><strong>{rate(overall.previous)}</strong><small>{overall.previous ? `${overall.previous.attempts}회 시도 · 성공 ${overall.previous.made} / 실패 ${overall.previous.missed}` : '비교 기록 없음'}</small></div>
        <div><span>성공률 변화</span><strong>{change(overall.rate_change_pp, '%p')}</strong><small>시도 수 {change(overall.attempt_change, '회')}</small></div>
      </div>
      <details className="practice-comparison"><summary>구역·슛 종류별 비교</summary>
        <div className="actions practice-tabs" role="group" aria-label="연습 비교 기준">
          {[['zone', '구역별'], ['type', '종류별'], ['zone_type', '구역·종류별']].map(([value, label]) =>
            <button type="button" className="button secondary" key={value} aria-pressed={kind === value} onClick={() => setKind(value)}>{label}</button>)}
        </div>
        <div className="practice-table-scroll"><table><thead><tr><th>항목</th><th>이번 성공/판정</th><th>이전 성공/판정</th><th>성공률 변화</th><th>시도 수 변화</th></tr></thead>
          <tbody>{rows.map(row => <tr key={row.key}><th>{row.label}</th>
            <td>{row.current.made}/{row.current.made + row.current.missed} · {rate(row.current)}{row.current.unresolved > 0 && <small>결과 확인 {row.current.unresolved}</small>}</td>
            <td>{row.previous?.attempts ? `${row.previous.made}/${row.previous.made + row.previous.missed} · ${rate(row.previous)}` : '기록 없음'}</td>
            <td>{change(row.rate_change_pp, '%p')}</td><td>{change(row.attempt_change, '회')}</td></tr>)}</tbody>
        </table></div>
      </details>
      {data.stale && <p className="inline-warning">슛 기록이나 비교 대상이 변경됐습니다. 최신 통계로 AI 요약을 갱신해 주세요.</p>}
      {report ? <div className="practice-ai-content">
        <p className="practice-ai-summary">{report.content.summary}</p>
        <div className="practice-ai-columns"><section><h3>이번 연습의 특징</h3><ul>{report.content.findings.map(observation)}</ul></section>
          <section><h3>다음 연습 과제</h3><ol>{report.content.next_practice.map(observation)}</ol></section></div>
      </div> : <p className="helper-text">{!data.ready ? '분석 완료 후 생성할 수 있습니다.' : !data.ai_available ? 'AI 요약을 사용하려면 OpenAI 연결 설정이 필요합니다.' : '생성 버튼을 누르면 저장된 통계를 OpenAI로 보내 특징과 다음 연습 과제를 작성합니다.'}</p>}
    </>}
  </section>
}
