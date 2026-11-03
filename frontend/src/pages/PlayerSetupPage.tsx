import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { AnalysisBackLink } from '../components/AnalysisBackLink'

const API_BASE = '/api'
type Analysis = { id: string; title: string; play_type: string; duration_ms: number; width: number; height: number }
type Player = { id: string; display_name: string; jersey_number: string | null; initial_time_ms: number | null; initial_box: number[] | null }
type PlayerData = { players: Player[]; warnings: string[]; max_players: number }

export function PlayerSetupPage() {
  const { analysisId = '' } = useParams(); const navigate = useNavigate()
  const [analysis, setAnalysis] = useState<Analysis | null>(null); const [data, setData] = useState<PlayerData>({ players: [], warnings: [], max_players: 1 })
  const [selected, setSelected] = useState(''); const [timeMs, setTimeMs] = useState(0); const [error, setError] = useState('')
  const [dirty, setDirty] = useState<string[]>([]); const [saving, setSaving] = useState(false)
  const [loadedFrameUrl, setLoadedFrameUrl] = useState(''); const [boxWidth, setBoxWidth] = useState(10); const [boxHeight, setBoxHeight] = useState(60)
  const frameUrl = useMemo(() => `${API_BASE}/analyses/${analysisId}/calibration/frame?time_ms=${timeMs}`, [analysisId, timeMs])
  const frameReady = loadedFrameUrl === frameUrl
  async function load() { const [a, p] = await Promise.all([fetch(`${API_BASE}/analyses/${analysisId}`), fetch(`${API_BASE}/analyses/${analysisId}/players`)]); if (!a.ok || !p.ok) throw new Error('선수 설정을 불러오지 못했습니다.'); setAnalysis(await a.json()); setData(await p.json()) }
  useEffect(() => {
    let active = true
    fetch(`${API_BASE}/analyses/${analysisId}`).then(async (analysisResponse) => {
      if (!analysisResponse.ok) throw new Error('선수 설정을 불러오지 못했습니다.')
      const details: Analysis = await analysisResponse.json()
      if (!active) return
      if (details.play_type === 'solo') { navigate(`/progress/${analysisId}`, { replace: true }); return }
      const playerResponse = await fetch(`${API_BASE}/analyses/${analysisId}/players`)
      if (!playerResponse.ok) throw new Error('선수 설정을 불러오지 못했습니다.')
      const players: PlayerData = await playerResponse.json()
      if (active) { setAnalysis(details); setData(players) }
    }).catch((caught) => { if (active) setError(caught.message) })
    return () => { active = false }
  }, [analysisId, navigate])
  useEffect(() => {
    if (!analysis) return
    const image = new Image(); image.onload = () => setLoadedFrameUrl(frameUrl); image.onerror = () => { setLoadedFrameUrl(''); setError('선수 지정 장면을 읽을 수 없습니다. 다른 시간을 선택해 주세요.') }; image.src = frameUrl
    return () => { image.onload = null; image.onerror = null }
  }, [analysis, frameUrl])
  async function add() { if (dirty.length) return setError('수정한 선수 정보를 먼저 저장해 주세요.'); setError(''); try { const response = await fetch(`${API_BASE}/analyses/${analysisId}/players`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }); const payload = await response.json(); if (!response.ok) throw new Error(payload.detail?.message ?? '선수를 추가하지 못했습니다.'); await load(); setSelected(payload.id) } catch (caught) { setError(caught instanceof Error ? caught.message : '선수를 추가하지 못했습니다.') } }
  async function update(player: Player, changes: Record<string, unknown>) { setSaving(true); setError(''); try { const response = await fetch(`${API_BASE}/analyses/${analysisId}/players/${player.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(changes) }); const payload = await response.json(); if (!response.ok) throw new Error(payload.detail?.message ?? '선수 정보를 저장하지 못했습니다.'); await load(); setDirty((current) => current.filter((id) => id !== player.id)) } catch (caught) { setError(caught instanceof Error ? caught.message : '선수 정보를 저장하지 못했습니다.') } finally { setSaving(false) } }
  async function assign(event: React.PointerEvent<HTMLDivElement>) { if (!frameReady) return setError('영상 장면을 먼저 확인해 주세요.'); if (dirty.length || saving) return setError('수정한 선수 정보를 먼저 저장해 주세요.'); const player = data.players.find((item) => item.id === selected); if (!player) return setError('먼저 지정할 선수를 선택해 주세요.'); const rect = event.currentTarget.getBoundingClientRect(); const width = boxWidth / 100, height = boxHeight / 100; const x = Math.max(0, Math.min(1 - width, (event.clientX - rect.left) / rect.width - width / 2)); const y = Math.max(0, Math.min(1 - height, (event.clientY - rect.top) / rect.height - height / 2)); await update(player, { initial_time_ms: timeMs, initial_box: [x, y, width, height] }) }
  function edit(playerId: string, field: 'display_name' | 'jersey_number', value: string) { setData((current) => ({ ...current, players: current.players.map((item) => item.id === playerId ? { ...item, [field]: value } : item) })); setDirty((current) => current.includes(playerId) ? current : [...current, playerId]) }
  if (!analysis) return <div className="page setup-page"><p role="status" className="inline-warning">{error || '촬영 형태를 확인하는 중입니다…'}</p></div>
  return <div className="page setup-page"><AnalysisBackLink to={`/court/${analysisId}`} /><header className="setup-header"><div><p className="eyebrow">PLAYER SETUP</p><h1>선수 설정</h1><p className="lead">이름과 등번호는 선택 사항입니다. 다인원 영상은 선수를 고른 뒤 화면 속 사람을 눌러 첫 위치를 지정하세요.</p></div><span className="step-chip">2 / 3 설정</span></header>
    <section className="player-setup-grid"><div className="setup-card"><div className="frame-toolbar"><strong>{analysis?.title}</strong><label>장면 이동<input type="range" min="0" max={Math.max(0, (analysis?.duration_ms ?? 0) - 500)} step="500" value={timeMs} onChange={(e) => { setTimeMs(Number(e.target.value)); setError('') }} /></label><span>{(timeMs/1000).toFixed(1)}초</span></div><div className="player-frame" style={{ backgroundImage: frameReady ? `url(${frameUrl})` : 'none', aspectRatio: analysis ? `${analysis.width} / ${analysis.height}` : '16 / 9' }} onPointerDown={assign}>{data.players.filter((p) => p.initial_box).map((p) => <button key={p.id} className={`player-box ${selected === p.id ? 'selected' : ''}`} style={{ left: `${p.initial_box![0]*100}%`, top: `${p.initial_box![1]*100}%`, width: `${p.initial_box![2]*100}%`, height: `${p.initial_box![3]*100}%` }} onPointerDown={(e) => e.stopPropagation()} onClick={() => setSelected(p.id)}>{p.display_name}</button>)}</div><p className="helper-text">{analysis?.play_type === 'solo' ? '개인 연습은 화면 위치를 지정하지 않아도 됩니다.' : '선수 선택 → 영상 속 사람 클릭 순서로 지정합니다.'}</p>{analysis?.play_type !== 'solo' && <div className="box-controls"><label>상자 너비 {boxWidth}%<input type="range" min="5" max="30" value={boxWidth} onChange={(e) => setBoxWidth(Number(e.target.value))} /></label><label>상자 높이 {boxHeight}%<input type="range" min="10" max="90" value={boxHeight} onChange={(e) => setBoxHeight(Number(e.target.value))} /></label></div>}</div>
      <aside className="setup-card player-panel"><div className="panel-title"><h2>선수 {data.players.length}/{data.max_players}</h2><button className="button secondary" onClick={add} disabled={data.players.length >= data.max_players || saving}>선수 추가</button></div>{data.players.map((player) => <div key={player.id} className={`player-row ${selected === player.id ? 'selected' : ''}`} onClick={() => setSelected(player.id)} onPointerDown={(event) => event.stopPropagation()}><input value={player.display_name} aria-label="선수 이름" onChange={(e) => edit(player.id, 'display_name', e.target.value)} maxLength={100}/><input value={player.jersey_number ?? ''} placeholder="등번호" aria-label="등번호" onChange={(e) => edit(player.id, 'jersey_number', e.target.value)} maxLength={20}/><span>{analysis?.play_type === 'solo' ? '개인 연습' : player.initial_box ? '위치 지정됨' : '위치 미지정'} · {dirty.includes(player.id) ? '저장 필요' : '저장됨'}</span><button className="button secondary" disabled={!dirty.includes(player.id) || saving} onClick={(event) => { event.stopPropagation(); update(player, { display_name: player.display_name, jersey_number: player.jersey_number }) }}>{saving ? '저장 중…' : '정보 저장'}</button></div>)}{data.warnings.map((warning) => <p className="inline-warning" key={warning}>{warning}</p>)}{error && <p className="form-error" role="alert">{error}</p>}<button className="button primary full-button" disabled={dirty.length > 0 || saving || !analysis} onClick={() => navigate(`/progress/${analysisId}`)}>설정 확인하고 분석 준비</button>{dirty.length > 0 && <p className="helper-text">수정한 선수 정보를 저장한 뒤 다음 단계로 이동할 수 있습니다.</p>}</aside></section>
  </div>
}
