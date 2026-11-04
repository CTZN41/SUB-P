import { readAnalysisResponse } from './analysisPolling.ts'

export type VideoUploadAttempt = { file: File; metadata: string; id: string; received: boolean }

export async function registerVideo<T>(form: FormData, file: File, attempt: { current: VideoUploadAttempt | null } = { current: null }): Promise<T> {
  const metadata = {
    title: String(form.get('title') ?? ''),
    play_type: String(form.get('play_type') ?? 'solo'),
    recorded_date: String(form.get('recorded_date') ?? ''),
    original_name: file.name,
    file_size: file.size,
    player_name: String(form.get('player_name') ?? '') || null,
    contains_others: form.get('contains_others') === 'true',
    consent_confirmed: form.get('consent_confirmed') === 'true',
    ai_notice_confirmed: form.get('ai_notice_confirmed') === 'true',
  }
  const serialized = JSON.stringify(metadata)
  if (attempt.current?.file !== file || attempt.current.metadata !== serialized) attempt.current = null
  if (!attempt.current) {
    const prepared = await fetch('/api/uploads', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: serialized,
    })
    // Support a frontend update while an older backend is finishing an analysis.
    if (prepared.status === 404 || prepared.status === 501) {
      return readAnalysisResponse<T>(await fetch('/api/analyses', { method: 'POST', body: form }), '영상을 등록하지 못했습니다.')
    }
    const upload = await readAnalysisResponse<{ id: string }>(prepared, '영상 등록 정보를 확인하지 못했습니다.')
    attempt.current = { file, metadata: serialized, id: upload.id, received: false }
  }
  const path = `/api/uploads/${encodeURIComponent(attempt.current.id)}`
  if (!attempt.current.received) {
    const transferred = await fetch(path, {
      method: 'PUT', headers: { 'Content-Type': 'application/octet-stream' }, body: file,
    })
    if (transferred.status === 410) attempt.current = null
    await readAnalysisResponse(transferred, '영상 전송을 완료하지 못했습니다.')
    if (attempt.current) attempt.current.received = true
  }
  const completed = await fetch(`${path}/complete`, { method: 'POST' })
  if (completed.status === 410) attempt.current = null
  const result = await readAnalysisResponse<T>(completed, '영상 등록을 완료하지 못했습니다.')
  attempt.current = null
  return result
}
