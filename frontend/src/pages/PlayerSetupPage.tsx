import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

const API_BASE = 'http://127.0.0.1:8000/api'
type Analysis = { id: string; title: string; play_type: string; duration_ms: number }
type Player = { id: string; display_name: string; jersey_number: string | null; initial_time_ms: number | null; initial_box: number[] | null }
type PlayerData = { players: Player[]; warnings: string[]; max_players: number }

export function PlayerSetupPage() {
  const { analysisId = '' } = useParams(); const navigate = useNavigate()
  const [analysis, setAnalysis] = useState<Analysis | null>(null); const [data, setData] = useState<PlayerData>({ players: [], warnings: [], max_players: 1 })
  const [selected, setSelected] = useState(''); const [timeMs, setTimeMs] = useState(0); const [error, setError] = useState('')
  const frameUrl = useMemo(() => `${API_BASE}/analyses/${analysisId}/calibration/frame?time_ms=${timeMs}`, [analysisId, timeMs])
  async function load() { const [a, p] = await Promise.all([fetch(`${API_BASE}/analyses/${analysisId}`), fetch(`${API_BASE}/analyses/${analysisId}/players`)]); if (!a.ok || !p.ok) throw new Error('선수 설정을 불러오지 못했습니다.'); setAnalysis(await a.json()); setData(await p.json()) }
  useEffect(() => {
    Promise.all([
      fetch(`${API_BASE}/analyses/${analysisId}`),
      fetch(`${API_BASE}/analyses/${analysisId}/players`),
    ]).then(async ([analysisResponse, playerResponse]) => {
      if (!analysisResponse.ok || !playerResponse.ok) throw new Error('선수 설정을 불러오지 못했습니다.')
      setAnalysis(await analysisResponse.json())
      setData(await playerResponse.json())
    }).catch((caught) => setError(caught.message))
  }, [analysisId])
  async function add() { setError(''); const response = await fetch(`${API_BASE}/analyses/${analysisId}/players`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }); const payload = await response.json(); if (!response.ok) return setError(payload.detail?.message); await load(); setSelected(payload.id) }
  async function update(player: Player, changes: Record<string, unknown>) { setError(''); const response = await fetch(`${API_BASE}/analyses/${analysisId}/players/${player.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(changes) }); const payload = await response.json(); if (!response.ok) return setError(payload.detail?.message); await load() }
  async function assign(event: React.PointerEvent<HTMLDivElement>) { const player = data.players.find((item) => item.id === selected); if (!player) return setError('먼저 지정할 선수를 선택해 주세요.'); const rect = event.currentTarget.getBoundingClientRect(); const width = .1, height = .22; const x = Math.max(0, Math.min(1 - width, (event.clientX - rect.left) / rect.width - width / 2)); const y = Math.max(0, Math.min(1 - height, (event.clientY - rect.top) / rect.height - height / 2)); await update(player, { initial_time_ms: timeMs, initial_box: [x, y, width, height] }) }
  return <div className="page setup-page"><header className="setup-header"><div><p className="eyebrow">PLAYER SETUP</p><h1>선수 설정</h1><p className="lead">이름과 등번호는 선택 사항입니다. 다인원 영상은 선수를 고른 뒤 화면 속 사람을 눌러 첫 위치를 지정하세요.</p></div><span className="step-chip">2 / 3 설정</span></header>
    <section className="player-setup-grid"><div className="setup-card"><div className="frame-toolbar"><strong>{analysis?.title}</strong><label>장면 이동<input type="range" min="0" max={analysis?.duration_ms ?? 0} step="500" value={timeMs} onChange={(e) => setTimeMs(Number(e.target.value))} /></label><span>{(timeMs/1000).toFixed(1)}초</span></div><div className="player-frame" style={{ backgroundImage: `url(${frameUrl})` }} onPointerDown={assign}>{data.players.filter((p) => p.initial_box).map((p) => <button key={p.id} className={`player-box ${selected === p.id ? 'selected' : ''}`} style={{ left: `${p.initial_box![0]*100}%`, top: `${p.initial_box![1]*100}%`, width: `${p.initial_box![2]*100}%`, height: `${p.initial_box![3]*100}%` }} onPointerDown={(e) => e.stopPropagation()} onClick={() => setSelected(p.id)}>{p.display_name}</button>)}</div><p className="helper-text">{analysis?.play_type === 'solo' ? '개인 연습은 화면 위치를 지정하지 않아도 됩니다.' : '선수 선택 → 영상 속 사람 클릭 순서로 지정합니다.'}</p></div>
      <aside className="setup-card player-panel"><div className="panel-title"><h2>선수 {data.players.length}/{data.max_players}</h2><button className="button secondary" onClick={add} disabled={data.players.length >= data.max_players}>선수 추가</button></div>{data.players.map((player) => <div key={player.id} className={`player-row ${selected === player.id ? 'selected' : ''}`} onClick={() => setSelected(player.id)} onPointerDown={(event) => event.stopPropagation()}><input value={player.display_name} aria-label="선수 이름" onChange={(e) => setData((current) => ({ ...current, players: current.players.map((item) => item.id === player.id ? { ...item, display_name: e.target.value } : item) }))} onBlur={() => update(player, { display_name: player.display_name })}/><input value={player.jersey_number ?? ''} placeholder="등번호" aria-label="등번호" onChange={(e) => setData((current) => ({ ...current, players: current.players.map((item) => item.id === player.id ? { ...item, jersey_number: e.target.value } : item) }))} onBlur={() => update(player, { jersey_number: player.jersey_number })}/><span>{player.initial_box ? '위치 지정됨' : '위치 미지정'}</span></div>)}{data.warnings.map((warning) => <p className="inline-warning" key={warning}>{warning}</p>)}{error && <p className="form-error">{error}</p>}<button className="button primary full-button" onClick={() => navigate(`/progress/${analysisId}`)}>설정 확인하고 분석 준비</button></aside></section>
  </div>
}
