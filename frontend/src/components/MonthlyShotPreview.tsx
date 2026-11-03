import { useEffect, useRef, useState } from 'react'
import { formatShotTime, shotCategory, SHOT_CATEGORY_LABELS } from '../utils/shotPresentation'
import type { CourtShot } from './ShotCourt3D'

type PreviewShot = CourtShot & { analysis_id: string }
type Segment = { id: string; sequence_start_ms: number; sequence_end_ms: number }

export function MonthlyShotPreview({ shot, number, typeLabel, onClose }: {
  shot: PreviewShot; number: number; typeLabel: string; onClose: () => void
}) {
  const [segment, setSegment] = useState<Segment | null>(null)
  const [error, setError] = useState('')
  const panelRef = useRef<HTMLElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  useEffect(() => {
    const controller = new AbortController()
    panelRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    fetch(`/api/analyses/${shot.analysis_id}/shots`, { signal: controller.signal })
      .then(async response => {
        if (!response.ok) throw new Error('슛 장면을 불러오지 못했습니다.')
        return response.json() as Promise<{ shots: Segment[] }>
      })
      .then(payload => {
        const found = payload.shots.find(item => item.id === shot.id)
        if (!found || found.sequence_end_ms <= found.sequence_start_ms) throw new Error('저장된 슛 구간을 확인할 수 없습니다.')
        setSegment(found)
      })
      .catch(caught => { if (caught.name !== 'AbortError') setError(caught.message) })
    return () => controller.abort()
  }, [shot.analysis_id, shot.id])
  function replay() {
    const video = videoRef.current
    if (!video || !segment) return
    video.currentTime = segment.sequence_start_ms / 1000
    video.play().catch(() => setError('영상의 재생 버튼을 눌러 주세요.'))
  }
  return <section ref={panelRef} className="setup-card monthly-shot-preview" aria-label="선택한 슛 영상">
    <div className="panel-title"><div><h2>{number}번 · {formatShotTime(shot.candidate_time_ms)}</h2>
      <p>{SHOT_CATEGORY_LABELS[shotCategory(shot)]} · {typeLabel} · {shot.zone_label ?? '위치 확인 필요'}</p>
    </div><button type="button" className="button secondary" onClick={onClose}>닫기</button></div>
    {error && <p role="alert">{error}</p>}
    {!segment && !error && <p role="status">슛 장면을 불러오는 중입니다…</p>}
    {segment && <video ref={videoRef} controls preload="metadata"
      src={`/api/analyses/${shot.analysis_id}/video`}
      onLoadedMetadata={replay} onEnded={replay}
      onTimeUpdate={() => {
        const video = videoRef.current
        if (video && !video.paused && video.currentTime >= segment.sequence_end_ms / 1000) video.currentTime = segment.sequence_start_ms / 1000
      }}
      onSeeked={() => {
        const video = videoRef.current
        if (video && (video.currentTime < segment.sequence_start_ms / 1000 || video.currentTime > segment.sequence_end_ms / 1000)) video.currentTime = segment.sequence_start_ms / 1000
      }}
      onError={() => setError('원본 영상을 재생하지 못했습니다.')}
    />}
  </section>
}
