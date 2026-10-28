import { Navigate, Route, Routes } from 'react-router-dom'
import './App.css'
import { AppLayout } from './components/AppLayout'
import { HomePage } from './pages/HomePage'
import { NewAnalysisPage } from './pages/NewAnalysisPage'
import { CourtCalibrationPage } from './pages/CourtCalibrationPage'
import { PlayerSetupPage } from './pages/PlayerSetupPage'
import { PlaceholderPage } from './pages/PlaceholderPage'

function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<HomePage />} />
        <Route path="new" element={<NewAnalysisPage />} />
        <Route path="court/:analysisId" element={<CourtCalibrationPage />} />
        <Route path="players/:analysisId" element={<PlayerSetupPage />} />
        <Route
          path="progress/:analysisId"
          element={
            <PlaceholderPage
              eyebrow="영상 분석"
              title="분석 진행 상태"
              description="실제 영상 분석과 진행률 표시는 이후 분석 Phase에서 연결합니다."
            />
          }
        />
        <Route
          path="results/:analysisId"
          element={
            <PlaceholderPage
              eyebrow="분석 결과"
              title="슈팅 맵과 히트맵"
              description="3D O/X 슈팅 맵과 2D 구역 히트맵은 Phase 9부터 구현합니다."
            />
          }
        />
        <Route
          path="history"
          element={
            <PlaceholderPage
              eyebrow="저장 기록"
              title="분석 기록"
              description="저장된 영상과 분석 결과 목록은 Phase 11에서 연결합니다."
            />
          }
        />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export default App
