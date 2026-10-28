import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

const API_BASE = 'http://127.0.0.1:8000/api'
const STAGES = ['영상 준비', '코트와 골대 확인', '선수·공 추적', '슛 후보 찾기', '성공·실패와 슛 종류 판단', '결과 계산', '완료']
type Analysis = { id: string; title: string; duration_ms: number }
type Job = { id: string; status: string; stage: string | null; processed_ms: number; is_simulation: boolean; error_message: string | null }

export function AnalysisProgressPage() {
  const { analysisId = '' } = useParams(); const [analysis, setAnalysis] = useState<Analysis | null>(null); const [job, setJob] = useState<Job | null>(null); const [error, setError] = useState(''); const [busy, setBusy] = useState(false)
  const loadJob = useCallback(async () => { const response = await fetch(`${API_BASE}/analyses/${analysisId}/jobs/current`); if (response.status === 404) return setJob(null); const payload = await response.json(); if (!response.ok) throw new Error(payload.detail?.message ?? '작업 상태를 불러오지 못했습니다.'); setJob(payload) }, [analysisId])
  useEffect(() => {
    fetch(`${API_BASE}/analyses/${analysisId}`).then(async (response) => {
      if (!response.ok) throw new Error('분석 기록을 불러오지 못했습니다.')
      setAnalysis(await response.json())
    }).catch((caught) => setError(caught.message))
    fetch(`${API_BASE}/analyses/${analysisId}/jobs/current`).then(async (response) => {
      if (response.status === 404) return null
      const payload = await response.json()
      if (!response.ok) throw new Error(payload.detail?.message ?? '작업 상태를 불러오지 못했습니다.')
      return payload
    }).then((payload) => { if (payload) setJob(payload) }).catch((caught) => setError(caught.message))
  }, [analysisId])
  useEffect(() => { if (!job || !['queued','running','cancel_requested'].includes(job.status)) return; const timer = window.setInterval(() => loadJob().catch((caught) => setError(caught.message)), 1000); return () => window.clearInterval(timer) }, [job, loadJob])
  async function start() { setBusy(true); setError(''); try { const response = await fetch(`${API_BASE}/analyses/${analysisId}/jobs/start`, { method: 'POST' }); const payload = await response.json(); if (!response.ok) throw new Error(payload.detail?.message ?? '분석을 시작하지 못했습니다.'); setJob(payload) } catch (caught) { setError(caught instanceof Error ? caught.message : '분석을 시작하지 못했습니다.') } finally { setBusy(false) } }
  async function cancel() { if (!job || !window.confirm('분석 흐름 점검을 취소할까요? 원본 영상과 설정은 남습니다.')) return; const response = await fetch(`${API_BASE}/analyses/${analysisId}/jobs/${job.id}/cancel`, { method: 'POST' }); setJob(await response.json()) }
  const activeIndex = Math.max(0, STAGES.indexOf(job?.stage ?? STAGES[0])); const terminal = job && ['succeeded','failed','canceled'].includes(job.status)
  return <div className="page progress-page"><header className="setup-header"><div><p className="eyebrow">ANALYSIS FLOW</p><h1>{analysis?.title ?? '분석 준비'}</h1><p className="lead">실제 슛 AI 분석 전, 시작·진행·취소·재시도 흐름을 확인하는 개발용 점검입니다.</p></div><span className="step-chip">3 / 3 설정</span></header>
    <div className="simulation-banner"><strong>개발용 흐름 점검</strong><span>실제 슛을 찾거나 결과를 저장하지 않습니다. Phase 7부터 실제 영상 분석기를 연결합니다.</span></div>
    <section className="setup-card progress-card"><div className="progress-summary"><div><span>현재 상태</span><strong>{job?.stage ?? '시작 전'}</strong></div><div><span>처리한 영상 구간</span><strong>{job ? `${(job.processed_ms/1000).toFixed(1)}초 / ${((analysis?.duration_ms ?? 0)/1000).toFixed(1)}초` : '-'}</strong></div></div>
      <ol className="stage-list">{STAGES.map((stage, index) => <li key={stage} className={index < activeIndex || job?.status === 'succeeded' ? 'done' : index === activeIndex && job ? 'active' : ''}><span>{index + 1}</span><strong>{stage}</strong></li>)}</ol>
      {job?.status === 'succeeded' && <p className="success-note">작업 흐름 점검이 끝났습니다. 가짜 슛 결과는 만들지 않았습니다.</p>}{job?.status === 'failed' && <p className="form-error">{job.error_message ?? '작업이 중단되었습니다. 다시 시도할 수 있습니다.'}</p>}{job?.status === 'canceled' && <p className="inline-warning">취소되었습니다. 원본 영상과 코트·선수 설정은 그대로 남아 있습니다.</p>}{error && <p className="form-error">{error}</p>}
      <div className="actions">{(!job || terminal) && <button className="button primary" onClick={start} disabled={busy}>{job ? '처음부터 다시 점검' : '분석 시작'}</button>}{job && !terminal && <button className="button secondary" onClick={cancel}>분석 취소</button>}<Link className="button secondary" to="/">화면 나가기</Link></div>
    </section>
  </div>
}
