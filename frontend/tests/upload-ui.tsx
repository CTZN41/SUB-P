// Local browser fixture: synthetic files only; never calls a real backend or AI.
import React, { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { NewAnalysisPage } from '../src/pages/NewAnalysisPage'
import '../src/index.css'
import '../src/App.css'

let uploadedName = ''
let requests = 0
window.fetch = async (input, options) => {
  requests += 1
  const url = String(input)
  if (url === '/api/uploads') return Response.json({ id: 'drop-fixture' })
  if (url === '/api/uploads/drop-fixture' && options?.body instanceof File) {
    uploadedName = options.body.name
    return Response.json({})
  }
  if (url === '/api/uploads/drop-fixture/complete') return Response.json({
    id: 'drop-fixture', title: '드롭 등록 시험', original_name: uploadedName,
    file_size: 12, duration_ms: 60000, width: 1920, height: 1080, fps: 30, warnings: [],
  })
  throw new Error('Real network requests are disabled in this fixture.')
}

function Fixture() {
  const [status, setStatus] = useState('실제 파일·API를 사용하지 않는 브라우저 시험')
  function drop(names: string[], target = '.file-picker') {
    const dataTransfer = new DataTransfer()
    names.forEach(name => dataTransfer.items.add(new File(['fixture-data'], name, { type: 'video/mp4' })))
    const zone = document.querySelector(target)!
    zone.dispatchEvent(new DragEvent('dragenter', { bubbles: true, cancelable: true, dataTransfer }))
    zone.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer }))
    const child = zone.querySelector('span')
    if (child) {
      child.dispatchEvent(new DragEvent('dragenter', { bubbles: true, cancelable: true, dataTransfer }))
      child.dispatchEvent(new DragEvent('dragleave', { bubbles: true, cancelable: true, dataTransfer }))
    }
    const prevented = !zone.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer }))
    setStatus(`기본 파일 열기 차단: ${prevented} · 시험 요청: ${requests}회`)
  }
  return <>
    <aside style={{ padding: 16, display: 'flex', gap: 12, flexWrap: 'wrap' }}>
      <button onClick={() => drop(['drop-test.mp4'])}>정상 MP4 드롭</button>
      <button onClick={() => drop(['wrong.mov'])}>잘못된 형식 드롭</button>
      <button onClick={() => drop(['a.mp4', 'b.mp4'])}>파일 두 개 드롭</button>
      <button onClick={() => drop(['outside.mp4'], '.registration-header')}>영역 밖 드롭</button>
      <output>{status}</output>
    </aside>
    <NewAnalysisPage />
  </>
}

createRoot(document.getElementById('root')!).render(<MemoryRouter><Fixture /></MemoryRouter>)
