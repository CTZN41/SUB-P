import { createContext, useContext } from 'react'
import type { Session, SupabaseClient } from '@supabase/supabase-js'
import type { AccountProfile } from './accountProfile'

export type AuthConfig = { required: boolean; configured: boolean; url: string | null; publishableKey: string | null; providers: Record<'google' | 'naver' | 'kakao', boolean> }
export type AuthState = { config: AuthConfig | null; client: SupabaseClient | null; session: Session | null; profile: AccountProfile | null; loading: boolean; ready: boolean; error: string; remember: boolean; setRemember: (value: boolean) => void; refreshProfile: () => Promise<void>; signOut: () => Promise<void> }
export const AuthContext = createContext<AuthState | null>(null)

export function useAuth() {
  const state = useContext(AuthContext)
  if (!state) throw new Error('AuthProvider is required')
  return state
}
