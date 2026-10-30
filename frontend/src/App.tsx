import { Navigate, Route, Routes } from 'react-router-dom'
import './App.css'
import { AppLayout } from './components/AppLayout'
import { HomePage } from './pages/HomePage'
import { NewAnalysisPage } from './pages/NewAnalysisPage'
import { CourtCalibrationPage } from './pages/CourtCalibrationPage'
import { PlayerSetupPage } from './pages/PlayerSetupPage'
import { AnalysisProgressPage } from './pages/AnalysisProgressPage'
import { HistoryPage } from './pages/HistoryPage'
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
        <Route path="history" element={<HistoryPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export default App
