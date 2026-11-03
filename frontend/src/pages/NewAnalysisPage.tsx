import { useRef, useState } from 'react'
import type { FormEvent, MouseEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { AnalysisBackLink } from '../components/AnalysisBackLink'

const API_BASE = 'http://127.0.0.1:8000/api'
const MAX_FILE_BYTES = 5 * 1024 * 1024 * 1024

type RegistrationResult = {
  id: string
  title: string
  original_name: string
  file_size: number
  duration_ms: number
  width: number | null
  height: number | null
  fps: number | null
  warnings: string[]
}

function formatBytes(bytes: number) {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)}GB`
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)}MB`
  return `${(bytes / 1024).toFixed(1)}KB`
}

function formatDuration(milliseconds: number) {
  const totalSeconds = Math.round(milliseconds / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}분 ${seconds}초`
}

export function NewAnalysisPage() {
  const navigate = useNavigate()
  const recordedDateRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [recordedDate, setRecordedDate] = useState('')
  const [playType, setPlayType] = useState('solo')
  const [containsOthers, setContainsOthers] = useState(false)
  const [consentConfirmed, setConsentConfirmed] = useState(false)
  const [aiNoticeConfirmed, setAiNoticeConfirmed] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<RegistrationResult | null>(null)

  function openRecordedDatePicker(event: MouseEvent<HTMLLabelElement>) {
    const input = recordedDateRef.current
    if (!input || !input.showPicker) return
    event.preventDefault()
    input.focus()
    try { input.showPicker() } catch { /* 이 경우 입력창에서 날짜를 직접 선택할 수 있습니다. */ }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError('')
    if (!title.trim() || !recordedDate || !recordedDateRef.current?.validity.valid) {
      setError('분석 이름과 촬영 날짜를 입력해 주세요.')
      return
    }
    if (!aiNoticeConfirmed || (containsOthers && !consentConfirmed)) {
      setError('필요한 동의 항목을 확인해 주세요.')
      return
    }
    if (!file) {
      setError('분석할 MP4 영상 한 개를 선택해 주세요.')
      return
    }
    if (!file.name.toLowerCase().endsWith('.mp4')) {
      setError('MP4 영상만 사용할 수 있습니다.')
      return
    }
    if (file.size > MAX_FILE_BYTES) {
      setError('영상 파일은 최대 5GB까지 등록할 수 있습니다.')
      return
    }

    const form = new FormData(event.currentTarget)
    form.set('title', title.trim())
    form.set('video', file)
    form.set('contains_others', String(containsOthers))
    form.set('consent_confirmed', String(consentConfirmed))
    form.set('ai_notice_confirmed', String(aiNoticeConfirmed))
    setSubmitting(true)
    try {
      const response = await fetch(`${API_BASE}/analyses`, { method: 'POST', body: form })
      const payload = await response.json()
      if (!response.ok) {
        throw new Error(payload.detail?.message ?? '영상을 등록하지 못했습니다.')
      }
      setResult(payload)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '영상을 등록하지 못했습니다.')
    } finally {
      setSubmitting(false)
    }
  }

  async function continueToCourt() {
    if (!result) return
    setError('')
    try {
      const response = await fetch(`${API_BASE}/analyses/${result.id}/accept-warnings`, {
        method: 'POST',
      })
      if (!response.ok) throw new Error('촬영 조건 확인을 저장하지 못했습니다.')
      navigate(`/court/${result.id}`)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '다음 단계로 이동하지 못했습니다.')
    }
  }

  if (result) {
    return (
      <div className="page registration-page">
        <AnalysisBackLink to="/history" />
        <section className="registration-result">
          <p className="eyebrow">영상 등록 완료</p>
          <h1>{result.title}</h1>
          <div className="video-facts">
            <span>{result.original_name}</span>
            <span>{formatBytes(result.file_size)}</span>
            <span>{formatDuration(result.duration_ms)}</span>
            <span>{result.width}×{result.height}</span>
            <span>{result.fps?.toFixed(2)}fps</span>
          </div>
          <div className="warning-panel">
            <h2>촬영 조건을 확인해 주세요</h2>
            <ul>{result.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
          </div>
          {error && <p className="form-error" role="alert">{error}</p>}
          <div className="actions">
            <button className="button primary" type="button" onClick={continueToCourt}>
              확인하고 코트 설정으로 이동
            </button>
            <button className="button secondary" type="button" onClick={() => { setResult(null); setFile(null) }}>
              새 분석 추가
            </button>
          </div>
        </section>
      </div>
    )
  }

  return (
    <div className="page registration-page">
      <AnalysisBackLink to="/" />
      <header className="registration-header">
        <div>
          <p className="eyebrow">NEW ANALYSIS</p>
          <h1>새 영상 등록</h1>
        </div>
        <div className="requirements-card">
          <strong>촬영 권장 조건</strong>
          <ul>
            <li>반코트, 골대와 림이 계속 보이게 촬영</li>
            <li>카메라를 고정하고 선수의 전신과 발 포함</li>
            <li>1080p 이상, 가능하면 60fps</li>
            <li>MP4 한 개, 최대 10분·5GB</li>
          </ul>
        </div>
      </header>

      <form className="registration-form registration-form-columns" onSubmit={submit}>
        <section className="form-section">
          <div className="section-heading"><span>01</span><div><h2>영상과 기본 정보</h2></div></div>
          <label>분석 이름 <small>필수</small><input name="title" value={title} onChange={(event) => setTitle(event.target.value)} maxLength={100} required placeholder="예: 10월 28일 개인 슈팅 연습" /></label>
          <div className="form-grid">
            <label>촬영 형태<select name="play_type" value={playType} onChange={(event) => setPlayType(event.target.value)}><option value="solo">개인 연습</option><option value="one_on_one">1대1</option><option value="two_on_two">2대2</option><option value="three_on_three">3대3</option></select></label>
            <label className="recorded-date-field" onClick={openRecordedDatePicker}>촬영 날짜 <small>필수</small><input ref={recordedDateRef} name="recorded_date" type="date" value={recordedDate} onChange={(event) => setRecordedDate(event.target.value)} required /></label>
            {playType !== 'solo' && <label>첫 선수 이름 <small>선택</small><input name="player_name" maxLength={100} placeholder="나머지 선수는 다음 단계에서 등록" /></label>}
          </div>
          <label className="file-picker">
            <span>{file ? file.name : 'MP4 파일을 선택하세요 (필수)'}</span>
            <small>{file ? formatBytes(file.size) : '한 번에 한 파일 · 최대 10분 · 최대 5GB'}</small>
            <input type="file" accept="video/mp4,.mp4" onChange={(event) => setFile(event.target.files?.[0] ?? null)} required />
          </label>
        </section>

        <section className="form-section">
          <div className="section-heading"><span>02</span><div><h2>촬영 동의와 AI 안내</h2></div></div>
          <fieldset>
            <legend>영상에 다른 사람이 포함되어 있나요?</legend>
            <label className="choice"><input type="radio" name="others_choice" checked={!containsOthers} onChange={() => { setContainsOthers(false); setConsentConfirmed(false) }} />아니요</label>
            <label className="choice"><input type="radio" name="others_choice" checked={containsOthers} onChange={() => setContainsOthers(true)} />예</label>
          </fieldset>
          {containsOthers && <label className="check-row"><input type="checkbox" checked={consentConfirmed} onChange={(event) => setConsentConfirmed(event.target.checked)} /><span>영상에 포함된 사람에게 촬영과 분석 동의를 받았습니다.</span></label>}
          <label className="check-row"><input type="checkbox" checked={aiNoticeConfirmed} onChange={(event) => setAiNoticeConfirmed(event.target.checked)} required /><span>전체 영상이 아닌 슛 후보 장면이 OpenAI로 처리될 수 있습니다.<br />이 안내를 확인했습니다.</span></label>
          <div className="consent-submit-actions">
            {error && <p className="form-error" role="alert">{error}</p>}
            <button className="button primary submit-button" type="submit" disabled={submitting || !title.trim() || !recordedDate || !file || (containsOthers && !consentConfirmed) || !aiNoticeConfirmed}>
              {submitting ? '영상 정보를 확인하는 중…' : '영상 등록하고 검사하기'}
            </button>
          </div>
        </section>
      </form>
    </div>
  )
}
