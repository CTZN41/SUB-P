// Isolated browser fixture for the real login component. No backend/provider requests.
import React from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import type { SupabaseClient } from '@supabase/supabase-js'
import { AuthContext } from '../src/auth/authContext'
import { AuthPage, AuthCallbackPage } from '../src/pages/AuthPage'
import '../src/index.css'
import '../src/App.css'

type LoginReply = { data: { url: string | null }; error: { code: string } | null }
const fixture = {
  mode: 'cancel', calls: [] as unknown[], remember: false,
  resolve: null as null | ((value: LoginReply) => void),
  emailResolve: null as null | ((value: { error: { code: string } }) => void),
  networkCalls: 0,
}
Object.assign(window, { authFixture: fixture })
window.fetch = async () => { fixture.networkCalls++; throw new Error('Real requests are disabled in this fixture.') }
const client = { auth: {
  signInWithOAuth: async (request: unknown) => {
    fixture.calls.push(request)
    if (fixture.mode === 'deferred') return new Promise<LoginReply>(resolve => { fixture.resolve = resolve })
    if (fixture.mode === 'error') return { data: { url: null }, error: { code: 'provider_disabled' } }
    if (fixture.mode === 'throw') throw new Error('Synthetic provider failure')
    if (fixture.mode === 'missing') return { data: { url: null }, error: null }
    // Hash navigation leaves the document/component alive, just as an aborted
    // external navigation can. Tests also exercise persisted pageshow events.
    return { data: { url: `${window.location.origin}${window.location.pathname}${window.location.search}#provider` }, error: null }
  },
  signInWithPassword: async () => new Promise(resolve => { fixture.emailResolve = resolve }),
} } as unknown as SupabaseClient
const auth = {
  config: { required: true, configured: true, url: null, publishableKey: null, providers: { google: true, kakao: true } },
  client, session: null, profile: null, loading: false, ready: false, error: '', remember: false,
  setRemember: (value: boolean) => { fixture.remember = value }, refreshProfile: async () => {}, signOut: async () => {},
}
const callback = new URLSearchParams(window.location.search).has('callback')
createRoot(document.getElementById('root')!).render(
  <MemoryRouter initialEntries={[callback ? '/auth/callback' : '/login?next=%2Fnew']}>
    <AuthContext.Provider value={auth}>
      <Routes><Route path="/login" element={<AuthPage />} /><Route path="/auth/callback" element={<AuthCallbackPage />} /></Routes>
    </AuthContext.Provider>
  </MemoryRouter>,
)
