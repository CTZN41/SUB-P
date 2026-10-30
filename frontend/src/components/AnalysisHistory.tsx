import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import './AnalysisHistory.css'

const API_BASE = 'http://127.0.0.1:8000/api'

type RecordItem = {
  id: string
  title: string
  original_name: string
  status: string
  created_at: string
  duration_ms: number
  video_available: boolean
  next_path: string
  state_label: string
  state_detail: string | null
}

export function AnalysisHistory({ recent = false }: { recent?: boolean }) {
  const [records, setRecords] = useState<RecordItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [deleting, setDeleting] = useState<RecordItem | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    const response = await fetch(`${API_BASE}/analyses`)
    if (!response.ok) throw new Error('분석 기록을 불러오지 못했습니다. 백엔드 연결을 확인해 주세요.')
    const payload: { analyses: RecordItem[] } = await response.json()
    setRecords(payload.analyses)
  }, [])

  useEffect(() => {
    void Promise.resolve().then(load).catch((caught) => setError(caught.message)).finally(() => setLoading(false))
  }, [load])

  async function remove() {
    if (!deleting) return
    setBusy(true)
    setError('')
    try {
      const response = await fetch(`${API_BASE}/analyses/${deleting.id}`, { method: 'DELETE' })
      if (!response.ok) {
        const payload = await response.json()
        throw new Error(payload.detail?.message ?? '기록을 삭제하지 못했습니다. 다시 시도해 주세요.')
      }
      setDeleting(null)
      setNotice('영상과 분석 결과를 삭제했습니다.')
      await load()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '기록을 삭제하지 못했습니다.')
      await load().catch(() => {})
    } finally {
      setBusy(false)
    }
  }

  const visible = recent ? records.slice(0, 3) : records
  return <section className="history-section" aria-label={recent ? '최근 분석 기록' : '분석 기록'}>
    <div className="history-heading"><h2>{recent ? '최근 분석 기록' : '저장된 분석 기록'}</h2>{recent && <Link to="/history">전체 보기</Link>}</div>
    {loading && <p>기록을 불러오는 중입니다.</p>}
    {!loading && !error && records.length === 0 && <p>아직 저장된 분석 기록이 없습니다.</p>}
    {notice && <p className="success-note" role="status">{notice}</p>}
    {error && <p className="form-error" role="alert">{error}</p>}
    <div className="history-list">{visible.map((item) => <article className="history-item" key={item.id}>
      <div><h3>{item.title}</h3><p>{new Date(item.created_at).toLocaleString('ko-KR')} · {item.original_name} · {(item.duration_ms / 1000).toFixed(1)}초</p>
        <span className={`history-state ${item.video_available ? '' : 'broken'}`}>{item.state_label}</span>
        {item.state_detail && <p className="history-detail">{item.state_detail}</p>}
      </div>
      <div className="history-actions">{item.video_available && <Link className="button secondary" to={item.next_path}>{item.status === 'completed' || item.status === 'review_required' ? '결과 보기' : '이어하기'}</Link>}
        <button className="button secondary" onClick={() => { setDeleting(item); setError(''); setNotice('') }}>삭제</button></div>
    </article>)}</div>
    {deleting && <div className="history-dialog-backdrop"><div className="history-dialog" role="dialog" aria-modal="true" aria-label="분석 기록 삭제 확인">
      <h2>‘{deleting.title}’ 삭제</h2><p>원본 영상과 코트 설정, 선수, 슛 기록, 분석 결과를 함께 삭제합니다. 복구할 수 없습니다.</p>
      <div className="actions"><button className="button secondary" disabled={busy} onClick={() => setDeleting(null)}>취소</button>
        <button className="button primary" disabled={busy} onClick={remove}>{busy ? '삭제 중…' : '영상과 분석 결과 삭제'}</button></div>
    </div></div>}
  </section>
}
