import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { AnalysisBackLink } from '../components/AnalysisBackLink'
import { ANALYSIS_STAGES as STAGES, analysisStageIndex, analysisStageLabel } from '../utils/analysisStages'
import { readAnalysisResponse, startAnalysisPolling } from '../utils/analysisPolling'

const API_BASE = '/api'
type Analysis = { id: string; title: string; duration_ms: number; play_type: string }
type Job = { id: string; status: string; stage: string | null; processed_ms: number; is_simulation: boolean; error_message: string | null; started_at: string | null; finished_at: string | null }

function durationLabel(seconds: number) {
  const value = Math.max(0, Math.floor(seconds))
  return `${Math.floor(value / 60)}분 ${value % 60}초`
}

export function AnalysisProgressPage() {
  const { analysisId = '' } = useParams()
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState('')
  const [statusError, setStatusError] = useState('')
  const [busy, setBusy] = useState(false)
  const [now, setNow] = useState(() => Date.now())
  const completedRealJobId = job && !job.is_simulation && job.status === 'succeeded' ? job.id : null
  const ticking = job !== null && ['queued', 'running', 'cancel_requested'].includes(job.status)

  useEffect(() => {
    if (!ticking) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [ticking])

  const loadJob = useCallback(async (signal: AbortSignal) => {
    const response = await fetch(`${API_BASE}/analyses/${analysisId}/jobs/current`, { signal })
    if (response.status === 404) return null
    const payload = await readAnalysisResponse<Job>(response, '작업 상태를 불러오지 못했습니다.')
    if (typeof payload.id !== 'string' || typeof payload.status !== 'string' || typeof payload.processed_ms !== 'number') {
      throw new Error('작업 상태를 불러오지 못했습니다.')
    }
    return payload
  }, [analysisId])

  useEffect(() => {
    const controller = new AbortController()
    setError('')
    fetch(`${API_BASE}/analyses/${analysisId}`, { signal: controller.signal })
      .then(response => readAnalysisResponse<Analysis>(response, '분석 기록을 불러오지 못했습니다.'))
      .then(payload => { if (!controller.signal.aborted) setAnalysis(payload) })
      .catch(() => { if (!controller.signal.aborted) setError('분석 기록을 불러오지 못했습니다.') })
    return () => controller.abort()
  }, [analysisId])

  useEffect(() => {
    const controller = new AbortController()
    const stop = startAnalysisPolling({
      request: () => loadJob(controller.signal),
      success: payload => { setJob(payload); setStatusError('') },
      failure: () => setStatusError('작업 상태를 잠시 불러오지 못했습니다. 자동으로 다시 확인하고 있습니다.'),
      keepPolling: payload => payload !== null && ['queued', 'running', 'cancel_requested'].includes(payload.status),
    })
    return () => { stop(); controller.abort() }
  }, [loadJob, job?.id])


  async function start() {
    setBusy(true); setError('')
    try {
      const response = await fetch(`${API_BASE}/analyses/${analysisId}/jobs/start-real`, { method: 'POST' })
      const payload = await readAnalysisResponse<Job>(response, '분석을 시작하지 못했습니다.')
      setJob(payload)
    } catch (caught) { setError(caught instanceof Error ? caught.message : '분석을 시작하지 못했습니다.') }
    finally { setBusy(false) }
  }

  async function cancel() {
    if (!job || !window.confirm('분석을 취소할까요? 원본 영상과 설정은 남습니다.')) return
    setBusy(true); setError('')
    try {
      const response = await fetch(`${API_BASE}/analyses/${analysisId}/jobs/${job.id}/cancel`, { method: 'POST' })
      const payload = await readAnalysisResponse<Job>(response, '취소하지 못했습니다. 다시 시도해 주세요.')
      setJob(payload)
    } catch (caught) { setError(caught instanceof Error ? caught.message : '취소하지 못했습니다. 다시 시도해 주세요.') }
    finally { setBusy(false) }
  }

  const terminal = !job || ['succeeded', 'failed', 'canceled'].includes(job.status)
  const activeIndex = analysisStageIndex(job?.stage)
  const simulation = job?.is_simulation ?? false
  const elapsed = job?.started_at ? ((job.finished_at ? Date.parse(job.finished_at) : now) - Date.parse(job.started_at)) / 1000 : null
  return <div className="page progress-page">
    <AnalysisBackLink to={!terminal ? '/history' : analysis?.play_type === 'solo' ? `/court/${analysisId}` : `/players/${analysisId}`} />
    <header className="setup-header"><div><p className="eyebrow">ANALYSIS FLOW</p><h1>{analysis?.title ?? '분석 준비'}</h1>
      <p className="lead">영상을 분석해 슛 위치와 결과를 확인합니다.</p></div><span className="step-chip">{analysis?.play_type === 'solo' ? '2 / 2 설정' : '3 / 3 설정'}</span></header>
    {simulation && <div className="simulation-banner"><strong>개발용 흐름 점검</strong><span>실제 슛을 찾거나 결과를 저장하지 않습니다.</span></div>}
    <section className="setup-card progress-card"><div className="progress-summary"><div><span>현재 상태</span><strong>{analysisStageLabel(job?.stage)}</strong></div>
      <div><span>{simulation ? '점검 화면의 예시 구간' : '처리한 원본 영상 구간'}</span><strong>{job ? `${(job.processed_ms / 1000).toFixed(1)}초 / ${((analysis?.duration_ms ?? 0) / 1000).toFixed(1)}초` : '-'}</strong></div>
      <div><span>{job?.finished_at ? '총 소요 시간' : '경과 시간'}</span><strong>{elapsed !== null && Number.isFinite(elapsed) ? durationLabel(elapsed) : '-'}</strong></div></div>
      <ol className="stage-list">{STAGES.map((stage, index) => <li key={stage} className={index < activeIndex || (simulation && job?.status === 'succeeded') ? 'done' : index === activeIndex && job ? 'active' : ''}><span>{index + 1}</span><strong>{stage}</strong></li>)}</ol>
      {job?.status === 'succeeded' && simulation && <p className="success-note">작업 흐름 점검이 끝났습니다. 가짜 슛 결과는 만들지 않았습니다.</p>}
      {job?.status === 'succeeded' && !simulation && <p className="success-note">분석이 완료되었습니다. 결과를 확인해주세요.</p>}
      {job?.status === 'failed' && <p className="form-error">{job.error_message ?? '분석이 중단되었습니다. 다시 시도할 수 있습니다.'}</p>}
      {job?.status === 'canceled' && <p className="inline-warning">취소되었습니다. 원본 영상과 코트·선수 설정은 그대로 남아 있습니다.</p>}
      {analysis && analysis.play_type !== 'solo' && <p className="inline-warning">실제 슛 후보 분석은 현재 개인 연습 영상만 지원합니다. 여러 선수의 첫 위치 지정과 개발용 흐름은 확인할 수 있습니다.</p>}
      {analysis && analysis.play_type !== 'solo' && <p className="helper-text">여러 선수의 슛은 원본 영상을 보면서 직접 추가하고 슈터를 지정할 수 있습니다. 자동 선수 추적은 아직 연결 중입니다.</p>}
      {error && <p className="form-error" role="alert">{error}</p>}
      {statusError && <p className="inline-warning" role="status">{statusError}</p>}
      {terminal && !completedRealJobId && <p className="helper-text">분석에는 OpenAI를 사용합니다.</p>}
      <div className="actions">{job?.status === 'succeeded' && !simulation && <Link className="button primary" to={`/results/${analysisId}`}>결과 확인</Link>}{analysis && analysis.play_type !== 'solo' && terminal && !completedRealJobId && <Link className="button primary" to={`/results/${analysisId}`}>수동으로 슛 기록하기</Link>}{terminal && !completedRealJobId && <button className="button primary" onClick={start} disabled={busy || analysis?.play_type !== 'solo'}>{busy ? '시작 중…' : '분석 시작'}</button>}
        {job && !terminal && <button className="button secondary" onClick={cancel} disabled={busy}>{busy ? '취소 요청 중…' : '분석 취소'}</button>}<Link className="button secondary" to="/">화면 나가기</Link></div>
    </section>
  </div>
}
