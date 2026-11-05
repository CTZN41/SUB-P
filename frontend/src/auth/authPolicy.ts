export function safeAuthNext(value: string | null | undefined): string {
  if (!value || !value.startsWith('/') || value.startsWith('//') || value.includes('\\') || [...value].some(char => char.charCodeAt(0) <= 32)) return '/'
  if (!/^\/(?:$|new(?:[/?#]|$)|profile(?:[/?#]|$)|history(?:[/?#]|$)|(?:results|court|players|progress)\/)/.test(value)) return '/'
  return value
}

export function socialLoginOptions(provider: 'google' | 'kakao', origin: string) {
  return { redirectTo: `${origin}/auth/callback`,
    // `scopes` appends to Supabase's Kakao defaults. On the deployed Auth
    // endpoint, the provider query parameter replaces scope instead. Never
    // override state, redirect_uri, client_id or PKCE parameters.
    ...(provider === 'kakao' ? { queryParams: { scope: 'profile_nickname' } } : {}),
  }
}

export function authMessage(code: string | undefined): string {
  const messages: Record<string, string> = {
    invalid_credentials: '이메일 또는 비밀번호를 확인해 주세요.',
    email_not_confirmed: '받은 메일에서 이메일 인증을 완료해 주세요.',
    user_already_exists: '가입 정보를 확인하거나 로그인해 주세요.',
    email_exists: '가입 정보를 확인하거나 로그인해 주세요.',
    email_address_not_authorized: '현재 이 이메일로 인증 메일을 보낼 수 없습니다. 관리자에게 문의해 주세요.',
    email_address_invalid: '이메일 주소를 확인해 주세요.',
    email_provider_disabled: '현재 이메일 회원가입을 사용할 수 없습니다.',
    signup_disabled: '현재 새 회원가입을 사용할 수 없습니다.',
    weak_password: '더 안전한 비밀번호를 입력해 주세요.',
    over_email_send_rate_limit: '인증 메일 발송 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.',
    over_request_rate_limit: '요청이 많습니다. 잠시 후 다시 시도해 주세요.',
    provider_disabled: '아직 이 간편 로그인을 사용할 수 없습니다.',
  }
  return messages[code ?? ''] ?? '로그인을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요.'
}
