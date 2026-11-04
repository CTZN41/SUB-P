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
import { MonthlyResultsPage } from './pages/MonthlyResultsPage'
import { AuthGate, AuthProvider } from './auth/AuthProvider'
import { AuthCallbackPage, AuthPage } from './pages/AuthPage'
import { ProfilePage } from './pages/ProfilePage'

function App() {
  return (
    <AuthProvider><Routes>
      <Route path="login" element={<AuthPage key="login" />} />
      <Route path="signup" element={<AuthPage key="signup" signup />} />
      <Route path="auth/callback" element={<AuthCallbackPage />} />
      <Route element={<AuthGate><AppLayout /></AuthGate>}>
        <Route index element={<HomePage />} />
        <Route path="new" element={<NewAnalysisPage />} />
        <Route path="profile" element={<ProfilePage />} />
        <Route path="court/:analysisId" element={<CourtCalibrationPage />} />
        <Route path="players/:analysisId" element={<PlayerSetupPage />} />
        <Route path="progress/:analysisId" element={<AnalysisProgressPage />} />
        <Route path="results/:analysisId" element={<ShotReviewPage />} />
        <Route path="history" element={<HistoryPage />} />
        <Route path="history/monthly" element={<MonthlyResultsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes></AuthProvider>
  )
}

export default App
