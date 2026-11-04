import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { deleteAnalysisRecords } from '../utils/deleteAnalysisRecords'
import './AnalysisHistory.css'
import { formatShotTime } from '../utils/shotPresentation'

const API_BASE = '/api'

export type RecordItem = {
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

export function AnalysisHistory({ recent = false, onRecordsChange }: { recent?: boolean; onRecordsChange?: (records: RecordItem[]) => void }) {
  const [records, setRecords] = useState<RecordItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [deleting, setDeleting] = useState<RecordItem[]>([])
  const dialogRef = useRef<HTMLDialogElement>(null)
  const deleteLock = useRef(false)
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

  useEffect(() => {
    if (!loading) onRecordsChange?.(records)
  }, [records, loading, onRecordsChange])

  useEffect(() => {
    if (deleting.length && !dialogRef.current?.open) dialogRef.current?.showModal()
  }, [deleting])

  async function remove() {
    if (!deleting.length || deleteLock.current) return
    deleteLock.current = true
    setBusy(true)
    setError('')
    try {
      const result = await deleteAnalysisRecords(deleting.map(item => item.id), async (id) => {
        const response = await fetch(`${API_BASE}/analyses/${id}`, { method: 'DELETE' })
        if (response.status === 404) return // Already removed in another tab.
        if (!response.ok) {
          const payload = await response.json().catch(() => null)
          throw new Error(payload?.detail?.message ?? '기록을 삭제하지 못했습니다. 다시 시도해 주세요.')
        }
      }, (id) => {
        setRecords(current => current.filter(item => item.id !== id))
        setSelectedIds(current => current.filter(selected => selected !== id))
      })
      if (result.failed.length) {
        const failedIds = new Set(result.failed.map(item => item.id))
        setDeleting(current => current.filter(item => failedIds.has(item.id)))
        setError(`${result.failed.length}개 기록을 삭제하지 못했습니다. ${result.failed[0].message}`)
      } else {
        setDeleting([])
      }
      if (result.removed.length) setNotice(`${result.removed.length}개 영상과 분석 결과를 삭제했습니다.`)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '기록을 삭제하지 못했습니다.')
    } finally {
      deleteLock.current = false
      setBusy(false)
    }
  }

  const visible = recent ? records.slice(0, 3) : records
  const selected = records.filter(item => selectedIds.includes(item.id))
  const allSelected = records.length > 0 && selected.length === records.length
  function requestDelete(items: RecordItem[]) { setDeleting(items); setError(''); setNotice('') }
  return <section className={`history-section${recent ? ' recent-history' : ''}`} aria-label={recent ? '최근 분석 기록' : '분석 기록'}>
    <div className="history-heading"><h2>{recent ? '최근 분석 기록' : '영상별 기록'}</h2>{recent && <Link to="/history">전체 보기</Link>}</div>
    {loading && <p>기록을 불러오는 중입니다.</p>}
    {!loading && !error && records.length === 0 && <p>아직 저장된 분석 기록이 없습니다.</p>}
    {notice && <p className="success-note" role="status">{notice}</p>}
    {error && <p className="form-error" role="alert">{error}</p>}
    {!recent && !loading && records.length > 0 && <div className="history-selection-toolbar">
      <label className="history-select-all"><input type="checkbox" checked={allSelected} disabled={busy}
        ref={element => { if (element) element.indeterminate = selected.length > 0 && !allSelected }}
        onChange={event => setSelectedIds(event.target.checked ? records.map(item => item.id) : [])} />전체 선택</label>
      <span className="history-selected-count" role="status">{selected.length}개 선택</span>
      <div className="history-selection-actions">
        {selected.length > 0 && <button className="button secondary" disabled={busy} onClick={() => setSelectedIds([])}>선택 해제</button>}
        <button className="button danger" disabled={busy || selected.length === 0} onClick={() => requestDelete(selected)}>선택 삭제{selected.length > 0 ? ` (${selected.length})` : ''}</button>
      </div>
    </div>}
    <div className="history-list">{visible.map((item, index) => <Fragment key={item.id}>
      {!recent && (index === 0 || new Date(visible[index - 1].created_at).toLocaleDateString('ko-KR') !== new Date(item.created_at).toLocaleDateString('ko-KR')) && <h3 className="history-day-heading">{new Date(item.created_at).toLocaleDateString('ko-KR')}</h3>}
      <article className={`history-item${selectedIds.includes(item.id) ? ' is-selected' : ''}`}>
      {recent && <div className="history-thumbnail" aria-hidden="true">
        {item.video_available && <img loading="lazy" src={`${API_BASE}/analyses/${item.id}/calibration/frame?time_ms=${Math.min(6000, Math.floor(item.duration_ms / 2))}`} alt="" onError={event => { event.currentTarget.hidden = true }} />}
        <span className="history-thumbnail-duration">{formatShotTime(item.duration_ms)}</span>
      </div>}
      {!recent && <input className="history-record-checkbox" type="checkbox" aria-label={`${item.title} 선택`} checked={selectedIds.includes(item.id)} disabled={busy}
        onChange={event => setSelectedIds(current => event.target.checked ? [...current, item.id] : current.filter(id => id !== item.id))} />}
      <div className="history-record-info"><h3>{item.title}</h3><p title={item.original_name}>{recent ? new Date(item.created_at).toLocaleDateString('ko-KR') : `${new Date(item.created_at).toLocaleString('ko-KR')} · ${item.original_name} · ${(item.duration_ms / 1000).toFixed(1)}초`}</p>
        <span className={`history-state ${item.video_available ? '' : 'broken'}`}>{item.state_label}</span>
        {item.state_detail && <p className="history-detail">{item.state_detail}</p>}
      </div>
      <div className="history-actions">{item.video_available && <Link className="button secondary" to={item.next_path}>{item.status === 'completed' || item.status === 'review_required' ? '결과 보기' : '이어하기'}</Link>}
        <button className="button secondary" disabled={busy} onClick={() => requestDelete([item])}>삭제</button></div>
    </article></Fragment>)}</div>
    {deleting.length > 0 && <dialog ref={dialogRef} className="history-dialog" aria-labelledby="history-delete-title" aria-describedby="history-delete-description" onCancel={event => { event.preventDefault(); if (!busy) setDeleting([]) }}>
      <h2 id="history-delete-title">{deleting.length === 1 ? `‘${deleting[0].title}’ 삭제` : `선택한 기록 ${deleting.length}개 삭제`}</h2>
      <p id="history-delete-description"><span className="message-line">원본 영상과 코트 설정, 선수, 슛 기록, 분석 결과를 함께 삭제합니다.</span><strong className="message-line">복구할 수 없습니다.</strong></p>
      <ul className="history-delete-list">{deleting.map(item => <li key={item.id}><strong>{item.title}</strong><span>{item.original_name}</span></li>)}</ul>
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="actions"><button className="button secondary" autoFocus disabled={busy} onClick={() => setDeleting([])}>취소</button>
        <button className="button primary" disabled={busy} onClick={remove}>{busy ? '삭제 중…' : '영상과 분석 결과 삭제'}</button></div>
    </dialog>}
  </section>
}
