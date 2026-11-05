import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import type { FormEvent } from 'react'
import { readAnalysisResponse } from '../utils/analysisPolling'
import './EmailNotification.css'

type NotificationState = {
  configured: boolean; ready: boolean; position_pending: number; outcome_pending: number
  status: 'waiting' | 'preparing' | 'sending' | 'accepted' | 'failed' | 'unknown' | 'canceled' | null
  email_available: boolean; account_email_masked: string | null; recipient_masked: string | null; error_code: string | null
  email_reports_consent: boolean
}
const PENDING = ['waiting', 'preparing', 'sending']

export function EmailNotification({ analysisId, revision, manual = false }: { analysisId: string; revision?: unknown; manual?: boolean }) {
  const [data, setData] = useState<NotificationState | null>(null)
  const [expanded, setExpanded] = useState(manual)
  const requestId = useRef(crypto.randomUUID())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const url = `/api/analyses/${analysisId}/email-notification`

  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    async function load() {
      try {
        const response = await fetch(url, { signal: controller.signal })
        const payload = await readAnalysisResponse<NotificationState>(response, '이메일 알림 상태를 불러오지 못했습니다.')
        if (controller.signal.aborted) return
        setData(payload)
        if (payload.status && PENDING.includes(payload.status)) timer = setTimeout(load, 15000)
      } catch {
        if (!controller.signal.aborted) setError('이메일 알림 상태를 불러오지 못했습니다.')
      }
    }
    void load()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [url, revision, refresh])

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!data?.email_reports_consent || busy) return
    setBusy(true); setError('')
    try {
      const response = await fetch(manual ? `${url}/send` : url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(manual ? { consent: true, request_id: requestId.current } : { consent: true }) })
      setData(await readAnalysisResponse<NotificationState>(response, '알림을 신청하지 못했습니다.'))
      setExpanded(false); setRefresh(value => value + 1)
      requestId.current = crypto.randomUUID()
    } catch (caught) { setError(caught instanceof Error ? caught.message : '알림을 신청하지 못했습니다.') }
    finally { setBusy(false) }
  }

  async function cancel() {
    setBusy(true); setError('')
    try {
      const response = await fetch(url, { method: 'DELETE' })
      setData(await readAnalysisResponse<NotificationState>(response, '알림 신청을 취소하지 못했습니다.'))
      setRefresh(value => value + 1)
    } catch (caught) { setError(caught instanceof Error ? caught.message : '알림 신청을 취소하지 못했습니다.') }
    finally { setBusy(false) }
  }

  const canApply = data?.configured && data.email_available && data.email_reports_consent && (!data.status || ['failed', 'canceled', ...(manual ? ['accepted'] : [])].includes(data.status))
  return <section className="panel email-notification" aria-label="이메일 결과 알림">
    <div className="email-notification-heading"><h2>{manual ? 'PDF 리포트 이메일 발송' : '이메일 결과 알림'}</h2>
      {canApply && <button type="button" className="button secondary" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>{expanded ? '닫기' : manual ? '다시 보내기' : '알림 신청'}</button>}
    </div>
    {!data && !error && <p className="helper-text">알림 상태를 불러오는 중입니다.</p>}
    {data && !data.configured && <p className="helper-text">이메일 알림을 준비하고 있습니다.</p>}
    {data && !data.email_available && <p className="helper-text"><Link to="/profile">내 프로필</Link>에서 이메일을 등록하고 인증을 완료해 주세요.</p>}
    {data?.email_available && !data.email_reports_consent && <p className="helper-text"><Link to="/profile">내 프로필</Link>에서 이메일 수신에 동의한 후 리포트를 보낼 수 있습니다.</p>}
    {data?.status === 'waiting' && <p role="status">{data.recipient_masked} · 위치·결과 확인이 모두 끝나면 히트맵과 연습 과제를 담은 PDF를 보내드립니다.</p>}
    {data?.status === 'preparing' && <p role="status">2D 히트맵과 연습 리포트 PDF를 준비하고 있습니다.</p>}
    {data?.status === 'sending' && <p role="status">이메일 발송을 요청하고 있습니다.</p>}
    {data?.status === 'accepted' && <p role="status">{manual ? '마지막 이메일 발송 요청이 접수됐습니다.' : '이메일 발송 요청이 접수됐습니다.'}</p>}
    {data?.status === 'unknown' && <p className="inline-warning">발송 여부 확인이 필요합니다. 관리자에게 문의해 주세요.</p>}
    {data?.status === 'failed' && <p className="inline-warning">{data.error_code === 'EMAIL_RECORD_CHANGED' ? '준비 중 슛 기록이 변경됐습니다. 확인 후 다시 신청해 주세요.' : data.error_code === 'EMAIL_PDF_FAILED' ? 'PDF를 준비하지 못했습니다. 관리자에게 문의해 주세요.' : '리포트 준비 또는 발송 요청을 완료하지 못했습니다. 확인 후 다시 신청해 주세요.'}</p>}
    {data?.status === 'waiting' && <button type="button" className="button secondary" disabled={busy} onClick={() => void cancel()}>신청 취소</button>}
    {canApply && expanded && <form className="email-notification-form" onSubmit={submit}>
      <p>받는 주소 · {data.account_email_masked}</p>
      {!data.ready && <p className="helper-text">위치와 결과 확인이 모두 완료된 후 발송됩니다.</p>}
      <button className="button primary" disabled={busy || !data.email_reports_consent || (manual && !data.ready)}>{busy ? '신청 중…' : manual ? '이메일로 보내기' : '이메일로 받기'}</button>
    </form>}
    {error && <p className="form-error" role="alert">{error} <button className="button secondary" type="button" onClick={() => { setError(''); setRefresh(value => value + 1) }}>다시 확인</button></p>}
  </section>
}
