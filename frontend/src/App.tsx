import { Navigate, Route, Routes } from 'react-router-dom'
import './App.css'
import { AppLayout } from './components/AppLayout'
import { HomePage } from './pages/HomePage'
import { NewAnalysisPage } from './pages/NewAnalysisPage'
import { CourtCalibrationPage } from './pages/CourtCalibrationPage'
import { PlayerSetupPage } from './pages/PlayerSetupPage'
import { AnalysisProgressPage } from './pages/AnalysisProgressPage'
import { PlaceholderPage } from './pages/PlaceholderPage'
import { ShotReviewPage } from './pages/ShotReviewPage'

function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<HomePage />} />
        <Route path="new" element={<NewAnalysisPage />} />
        <Route path="court/:analysisId" element={<CourtCalibrationPage />} />
        <Route path="players/:analysisId" element={<PlayerSetupPage />} />
        <Route path="progress/:analysisId" element={<AnalysisProgressPage />} />
        <Route path="results/:analysisId" element={<ShotReviewPage />} />
        <Route
          path="history"
          element={
            <PlaceholderPage
              eyebrow="저장 기록"
              title="분석 기록"
              description="저장된 영상과 분석 결과 목록은 Phase 12에서 연결합니다."
            />
          }
        />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export default App
