import { useEffect, useRef, useState } from 'react'
import type { CourtShot } from './ShotCourt3D'
import { formatShotTime } from '../utils/shotPresentation'
import { SHOT_TYPE_LABELS } from '../utils/shotTypes'
import './ShotPractice.css'

export type PlaylistShot = CourtShot & { analysis_id: string }
type Segment = { id: string; sequence_start_ms: number; sequence_end_ms: number }

export function ShotPlaylist({ shots, numbers, open, onStart, onClose }: {
  shots: PlaylistShot[]
  numbers: Record<string, number>
  open: boolean
  onStart: () => void
  onClose: () => void
}) {
  return <section className="shot-playlist" aria-label="선택한 슛 이어보기">
    <div className="panel-title"><div><h2>선택한 슛 이어보기</h2><p>{shots.length}개 장면</p></div>
      <button type="button" className="button secondary" disabled={!shots.length} onClick={open ? onClose : onStart}>
        {open ? '이어보기 닫기' : '이어보기 시작'}
      </button>
    </div>
    {open && shots.length > 0 && <PlaylistPlayer shots={shots} numbers={numbers} />}
  </section>
}

function PlaylistPlayer({ shots, numbers }: { shots: PlaylistShot[]; numbers: Record<string, number> }) {
  const [index, setIndex] = useState(0)
  const [loaded, setLoaded] = useState<{ analysisId: string; segments: Segment[] } | null>(null)
  const [error, setError] = useState('')
  const [finished, setFinished] = useState(false)
  const cache = useRef(new Map<string, Segment[]>())
  const videoRef = useRef<HTMLVideoElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const advancing = useRef(false)
  const shot = shots[index]
  const segment = loaded?.analysisId === shot.analysis_id ? loaded.segments.find(item => item.id === shot.id) : null

  useEffect(() => {
    panelRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    const cached = cache.current.get(shot.analysis_id)
    if (cached) {
      setLoaded({ analysisId: shot.analysis_id, segments: cached })
      return
    }
    fetch(`/api/analyses/${shot.analysis_id}/shots`, { signal: controller.signal })
      .then(async response => {
        if (!response.ok) throw new Error('슛 장면을 불러오지 못했습니다. 다음 장면을 선택해 주세요.')
        return response.json() as Promise<{ shots: Segment[] }>
      })
      .then(payload => {
        cache.current.set(shot.analysis_id, payload.shots)
        setLoaded({ analysisId: shot.analysis_id, segments: payload.shots })
      })
      .catch(caught => { if (caught.name !== 'AbortError') setError(caught.message) })
    return () => controller.abort()
  }, [shot.analysis_id])

  useEffect(() => {
    advancing.current = false
    setError('')
    if (loaded?.analysisId !== shot.analysis_id) return
    if (!segment || segment.sequence_end_ms <= segment.sequence_start_ms) {
      setError('저장된 슛 구간을 확인할 수 없습니다. 다음 장면을 선택해 주세요.')
      return
    }
    const video = videoRef.current
    if (video && video.readyState >= 1) {
      video.currentTime = segment.sequence_start_ms / 1000
      video.play().catch(() => setError('영상의 재생 버튼을 눌러 주세요.'))
    }
  }, [shot.id, shot.analysis_id, segment, loaded])

  function move(next: number) {
    videoRef.current?.pause()
    setFinished(false)
    setIndex(next)
  }
  function advance() {
    if (advancing.current || finished) return
    advancing.current = true
    videoRef.current?.pause()
    if (index + 1 < shots.length) move(index + 1)
    else setFinished(true)
  }
  function prepare() {
    const video = videoRef.current
    if (!video || !segment) return
    video.currentTime = segment.sequence_start_ms / 1000
    video.play().catch(() => setError('영상의 재생 버튼을 눌러 주세요.'))
  }
  return <div ref={panelRef} className="shot-playlist-player">
    <div className="panel-title"><div><strong>{index + 1} / {shots.length} · {numbers[shot.id]}번 · {formatShotTime(shot.candidate_time_ms)}</strong>
      <p>{SHOT_TYPE_LABELS[shot.shot_type] ?? shot.shot_type} / {shot.zone_label ?? '위치 확인 필요'}</p>
    </div><div className="actions">
      <button type="button" className="button secondary" disabled={index === 0} onClick={() => move(index - 1)}>이전 장면</button>
      <button type="button" className="button secondary" disabled={index + 1 >= shots.length} onClick={() => move(index + 1)}>다음 장면</button>
    </div></div>
    {error && <p className="form-error" role="alert">{error}</p>}
    {!segment && !error && <p role="status">슛 장면을 불러오는 중입니다…</p>}
    {segment && <video key={shot.analysis_id} ref={videoRef} controls preload="metadata"
      src={`/api/analyses/${shot.analysis_id}/video`} onLoadedMetadata={prepare}
      onTimeUpdate={() => {
        const video = videoRef.current
        if (video && !video.paused && !video.seeking && video.currentTime >= segment.sequence_end_ms / 1000) advance()
      }} onEnded={advance} onError={() => setError('원본 영상을 재생하지 못했습니다. 다음 장면을 선택해 주세요.')} />}
    {finished && <div className="actions" role="status"><span>이어보기가 끝났습니다.</span>
      <button type="button" className="button secondary" onClick={() => {
        advancing.current = false
        move(0)
        if (index === 0) prepare()
      }}>처음부터 다시 보기</button>
    </div>}
  </div>
}
