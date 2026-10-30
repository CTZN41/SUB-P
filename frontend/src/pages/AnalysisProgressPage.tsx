import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

const API_BASE = 'http://127.0.0.1:8000/api'
const STAGES = ['영상 준비', '코트와 골대 확인', '선수·공 추적', '슛 후보 찾기', '성공·실패와 슛 종류 판단', '결과 계산', '완료']
type Analysis = { id: string; title: string; duration_ms: number; play_type: string }
type Job = { id: string; status: string; stage: string | null; processed_ms: number; is_simulation: boolean; error_message: string | null }
type Candidate = { time_ms: number; foot_point_px: number[]; confidence: number; review_reasons: string[] }
type CandidateResult = { notice: string; source_fps: number; sample_fps: number; candidates: Candidate[]; issues: Record<string, number> }

export function AnalysisProgressPage() {
  const { analysisId = '' } = useParams()
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  const [result, setResult] = useState<CandidateResult | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const completedRealJobId = job && !job.is_simulation && job.status === 'succeeded' ? job.id : null

  const loadJob = useCallback(async () => {
    const response = await fetch(`${API_BASE}/analyses/${analysisId}/jobs/current`)
    if (response.status === 404) { setJob(null); return }
    const payload = await response.json()
    if (!response.ok) throw new Error(payload.detail?.message ?? '작업 상태를 불러오지 못했습니다.')
    setJob(payload)
  }, [analysisId])

  useEffect(() => {
    fetch(`${API_BASE}/analyses/${analysisId}`).then(async response => {
      if (!response.ok) throw new Error('분석 기록을 불러오지 못했습니다.')
      setAnalysis(await response.json())
    }).catch(caught => setError(caught.message))
    fetch(`${API_BASE}/analyses/${analysisId}/jobs/current`).then(async response => {
      if (response.status === 404) return null
      const payload = await response.json()
      if (!response.ok) throw new Error(payload.detail?.message ?? '작업 상태를 불러오지 못했습니다.')
      return payload
    }).then(payload => { if (payload) setJob(payload) }).catch(caught => setError(caught.message))
  }, [analysisId])

  useEffect(() => {
    if (!job || !['queued', 'running', 'cancel_requested'].includes(job.status)) return
    const timer = window.setInterval(() => loadJob().catch(caught => setError(caught.message)), 1000)
    return () => window.clearInterval(timer)
  }, [job, loadJob])

  useEffect(() => {
    if (!completedRealJobId) return
    fetch(`${API_BASE}/analyses/${analysisId}/phase7-candidates`).then(async response => {
      if (!response.ok) throw new Error('슛 후보를 불러오지 못했습니다.')
      setResult(await response.json())
    }).catch(caught => setError(caught.message))
  }, [analysisId, completedRealJobId])

  async function start(real: boolean) {
    setBusy(true); setError(''); setResult(null)
    try {
      const route = real ? 'start-real' : 'start'
      const response = await fetch(`${API_BASE}/analyses/${analysisId}/jobs/${route}`, { method: 'POST' })
      const payload = await response.json()
      if (!response.ok) throw new Error(payload.detail?.message ?? '분석을 시작하지 못했습니다.')
      setJob(payload)
    } catch (caught) { setError(caught instanceof Error ? caught.message : '분석을 시작하지 못했습니다.') }
    finally { setBusy(false) }
  }

  async function cancel() {
    if (!job || !window.confirm('분석을 취소할까요? 원본 영상과 설정은 남습니다.')) return
    setBusy(true); setError('')
    try {
      const response = await fetch(`${API_BASE}/analyses/${analysisId}/jobs/${job.id}/cancel`, { method: 'POST' })
      const payload = await response.json()
      if (!response.ok) throw new Error(payload.detail?.message ?? '취소하지 못했습니다. 다시 시도해 주세요.')
      setJob(payload)
    } catch (caught) { setError(caught instanceof Error ? caught.message : '취소하지 못했습니다. 다시 시도해 주세요.') }
    finally { setBusy(false) }
  }

  const terminal = !job || ['succeeded', 'failed', 'canceled'].includes(job.status)
  const activeIndex = Math.max(0, STAGES.indexOf(job?.stage ?? STAGES[0]))
  const simulation = job?.is_simulation ?? false
  return <div className="page progress-page">
    <header className="setup-header"><div><p className="eyebrow">ANALYSIS FLOW</p><h1>{analysis?.title ?? '분석 준비'}</h1>
      <p className="lead">실제 영상에서 슛 후보를 찾거나 개발용 작업 흐름을 점검합니다.</p></div><span className="step-chip">3 / 3 설정</span></header>
    <div className="simulation-banner"><strong>{simulation ? '개발용 흐름 점검' : 'Phase 7 슛 후보 분석'}</strong>
      <span>{simulation ? '실제 슛을 찾거나 결과를 저장하지 않습니다.' : '초기 학습 모델이 찾은 후보입니다. 실제 슛인지 직접 확인해야 하며 성공 여부와 슛 종류는 아직 판단하지 않습니다.'}</span></div>
    <section className="setup-card progress-card"><div className="progress-summary"><div><span>현재 상태</span><strong>{job?.stage ?? '시작 전'}</strong></div>
      <div><span>{simulation ? '점검 화면의 예시 구간' : '처리한 원본 영상 구간'}</span><strong>{job ? `${(job.processed_ms / 1000).toFixed(1)}초 / ${((analysis?.duration_ms ?? 0) / 1000).toFixed(1)}초` : '-'}</strong></div></div>
      <ol className="stage-list">{STAGES.map((stage, index) => <li key={stage} className={index < activeIndex || (simulation && job?.status === 'succeeded') ? 'done' : index === activeIndex && job ? 'active' : ''}><span>{index + 1}</span><strong>{stage}</strong></li>)}</ol>
      {job?.status === 'succeeded' && simulation && <p className="success-note">작업 흐름 점검이 끝났습니다. 가짜 슛 결과는 만들지 않았습니다.</p>}
      {job?.status === 'succeeded' && !simulation && <p className="success-note">후보 분석과 슛 기록 저장이 끝났습니다. 결과 화면에서 원본 장면을 확인해 주세요.</p>}
      {job?.status === 'failed' && <p className="form-error">{job.error_message ?? '분석이 중단되었습니다. 다시 시도할 수 있습니다.'}</p>}
      {job?.status === 'canceled' && <p className="inline-warning">취소되었습니다. 원본 영상과 코트·선수 설정은 그대로 남아 있습니다.</p>}
      {analysis && analysis.play_type !== 'solo' && <p className="inline-warning">실제 슛 후보 분석은 현재 개인 연습 영상만 지원합니다. 여러 선수의 첫 위치 지정과 개발용 흐름은 확인할 수 있습니다.</p>}
      {analysis && analysis.play_type !== 'solo' && <p className="helper-text">여러 선수의 슛은 원본 영상을 보면서 직접 추가하고 슈터를 지정할 수 있습니다. 자동 선수 추적은 아직 연결 중입니다.</p>}
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="actions">{job?.status === 'succeeded' && !simulation && <Link className="button primary" to={`/results/${analysisId}`}>슛 기록 검토하기</Link>}{analysis && analysis.play_type !== 'solo' && terminal && <Link className="button primary" to={`/results/${analysisId}`}>수동으로 슛 기록하기</Link>}{terminal && <><button className="button secondary" onClick={() => start(true)} disabled={busy || analysis?.play_type !== 'solo'}>{job ? '실제 후보 다시 찾기' : '실제 슛 후보 찾기'}</button><button className="button secondary" onClick={() => start(false)} disabled={busy}>개발용 흐름 점검</button></>}
        {job && !terminal && <button className="button secondary" onClick={cancel} disabled={busy}>{busy ? '취소 요청 중…' : '분석 취소'}</button>}<Link className="button secondary" to="/">화면 나가기</Link></div>
    </section>
    {result && <section className="setup-card progress-card" aria-label="슛 후보 결과"><h2>찾은 슛 후보 {result.candidates.length}개</h2><p>{result.notice}</p>
      <p>원본 {result.source_fps}fps · 분석 간격 {result.sample_fps}fps</p>
      {result.candidates.length === 0 && <p className="inline-warning">자동으로 찾은 슛이 없습니다. 원본 영상과 촬영 조건을 확인해 주세요.</p>}
      <ol>{result.candidates.map((candidate, index) => <li key={`${candidate.time_ms}-${index}`}>{(candidate.time_ms / 1000).toFixed(1)}초 · 선수 발 화면 좌표 ({candidate.foot_point_px.join(', ')}) · 탐지 신뢰도 {(candidate.confidence * 100).toFixed(0)}%{candidate.review_reasons.length > 0 && ` · ${candidate.review_reasons.join(', ')}`}</li>)}</ol>
      {Object.keys(result.issues).length > 0 && <><h3>확인할 점</h3><ul>{Object.entries(result.issues).map(([reason, count]) => <li key={reason}>{reason}: {count}회</li>)}</ul></>}
    </section>}
  </div>
}
