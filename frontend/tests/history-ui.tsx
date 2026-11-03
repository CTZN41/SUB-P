// Local Vite browser fixture. No requests reach the backend and no real files are removed.
import React from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { AnalysisHistory } from '../src/components/AnalysisHistory'
import '../src/index.css'
import '../src/App.css'

const records = [
  { id: 'fixture-a', title: '시험 기록 A', original_name: 'SUB-P-fixed-32s-60fps-outcome-test.mp4', status: 'completed' },
  { id: 'fixture-b', title: '시험 기록 B — 아주 긴 분석 제목과 원본 파일명 줄바꿈 확인', original_name: 'a'.repeat(170) + '.mp4', status: 'completed' },
  { id: 'fixture-active', title: '분석 중인 시험 기록', original_name: 'fixture-active.mp4', status: 'running' },
].map(item => ({ ...item, created_at: '2026-11-03T05:00:00Z', duration_ms: 32000, video_available: false,
  next_path: '#', state_label: item.status === 'running' ? '분석 중' : '완료', state_detail: null }))

window.fetch = async (input, options) => {
  const url = String(input)
  if (url === 'http://127.0.0.1:8000/api/analyses' && !options?.method) return Response.json({ analyses: records })
  if (url.startsWith('http://127.0.0.1:8000/api/analyses/fixture-') && options?.method === 'DELETE') {
    const id = url.split('/').pop()
    if (id === 'fixture-active') return Response.json({ detail: { message: '분석 중에는 삭제할 수 없습니다. 먼저 취소해 주세요.' } }, { status: 409 })
    return new Response(null, { status: 204 })
  }
  throw new Error('이 시험 화면에서는 실제 API 요청을 보내지 않습니다.')
}

createRoot(document.getElementById('root')!).render(
  <MemoryRouter><main className="page history-page"><p className="inline-warning">별도 UI 시험 화면 · 실제 기록과 API에 연결되지 않습니다.</p><h1>분석 기록</h1><AnalysisHistory /></main></MemoryRouter>,
)
