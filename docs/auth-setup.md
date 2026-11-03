# ShotLog 로그인 연결 안내

기준일: 2026-11-03. Google 설정 완료 후 로컬 로그인 보호와 Google 제공사를 켜고 백엔드를 재시작했다. Chrome에서 로그인된 홈·로그아웃 버튼과 새로고침 후 로그인 유지를 확인했다. 네이버·카카오와 실제 이메일 가입은 미검증이다. Google 계정 선택·동의 과정 전체를 이번 도구 조작으로 수행한 것은 아니다. 비밀 키를 채팅이나 Git에 올리지 않는다.

## 지금 확인할 화면

- 로그인: http://127.0.0.1:5173/login
- 회원가입: http://127.0.0.1:5173/signup
- 설정 전에는 연결 준비 안내가 나오고 실제 가입·간편 로그인 요청 버튼은 비활성화된다. 기존 로컬 분석은 계속 사용할 수 있다.

## 1. 먼저 Supabase 값 두 개 준비

사용 중인 Supabase 프로젝트의 Settings → API Keys에서 아래 값을 확인하고 **backend/.env 맨 아래**에 입력한다. 데이터베이스 비밀번호나 OpenAI 키와 다른 값이다.

| 항목 | 입력할 값 |
|---|---|
| SUPABASE_URL | 프로젝트 URL. 예: https://프로젝트식별자.supabase.co |
| SUPABASE_PUBLISHABLE_KEY | 공개용 publishable 키 또는 이전 형식 anon 키 |
| AUTH_FRONTEND_ORIGIN | 지금은 http://127.0.0.1:5173 |

`service_role`·secret 키는 공개용 키 자리에 넣지 않는다. 코드에서 해당 키를 거절하며, 공개용 URL/키만 브라우저에 전달한다. 세 제공사의 Client Secret은 프론트 코드나 VITE_ 환경변수에 넣지 않는다.

Supabase Authentication → URL Configuration에서 Site URL은 `http://127.0.0.1:5173`로 설정하고 다음 두 Redirect URL을 허용한다.

- http://127.0.0.1:5173/auth/callback
- http://localhost:5173/auth/callback

이메일 가입은 Supabase의 이메일 제공사를 사용한다. 이메일 확인을 켜 두고 비밀번호 최소 길이를8자 이상으로 설정한다. 실제 사용자에게 보낼 때는 발신 메일/SMTP와 발송 제한도 준비한다. 회원가입은 이메일·비밀번호·비밀번호 확인만 입력하며, 인증 메일을 확인한 뒤 로그인한다.

## 2. Google 앱 만들기

1. Google Cloud의 Google Auth Platform에서 ShotLog 앱과 OAuth 웹 클라이언트를 만든다.
2. 필요한 범위는 기본 프로필·이메일·openid다. 테스트 기간에는 자신의 계정을 테스트 사용자로 등록한다.
3. Supabase Authentication → Sign In / Providers → Google에 표시되는 **Callback URL**을 Google의 승인된 리디렉션 URI에 등록한다. 위의 프론트 주소와 다른, Supabase의 주소다.
4. Google이 발급한 Client ID와 Client Secret은 Supabase의 Google 설정에 입력하고 제공사를 켠다.
5. backend/.env의 `AUTH_GOOGLE_ENABLED=true`로 바꾼다.

## 3. 카카오 앱 만들기

1. Kakao Developers에서 ShotLog 앱을 만들고 카카오 로그인을 활성화한다.
2. REST API 키와 카카오 로그인 Client Secret을 준비한다.
3. Supabase Kakao 제공사에 표시되는 Callback URL을 카카오의 Redirect URI에 등록한다.
4. 키와 Secret을 Supabase의 Kakao 설정에 입력한다. 필요한 프로필 동의 항목을 설정한다.
5. 이메일 제공 권한이 없는 앱이라면 account_email을 요청하지 않고 Supabase Kakao의 **이메일 없는 사용자 허용**을 켠다. 이메일을 임의로 만들거나 인증됐다고 표시하지 않는다.
6. backend/.env의 `AUTH_KAKAO_ENABLED=true`로 바꾼다.

## 4. 네이버 앱과 사용자 지정 연결

네이버는 Supabase의 기본 Google/Kakao 항목처럼 바로 선택하는 방식이 아니다. 네이버의 중첩 프로필과 토큰 교환용 state를 처리하는 연결 코드를 FastAPI에 준비했다.

**중요:** Supabase 서버가 이 연결 주소를 호출하므로 네이버 연결에는 외부에서 접근할 수 있는 HTTPS API 주소가 필요하다. 현재 PC의127.0.0.1은 Supabase가 접근할 수 없다. 실제 네이버 계정 로그인은 API 주소와 앱 설정이 준비된 다음 검증한다. 이번 작업에서 서버를 공개하거나 터널을 만들지는 않았다.

1. NAVER Developers에서 네이버 로그인 앱을 만든다.
2. Client ID와 Client Secret을 backend/.env의 `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`에 입력한다.
3. Supabase Auth Providers → New Provider에서 Manual OAuth2 연결을 만든다. 식별자는 `custom:naver`, 이름은 네이버로 지정한다.
4. Supabase에 표시된 Callback URL을 네이버 앱의 Callback URL과 backend/.env의 `NAVER_REDIRECT_URI`에 동일하게 입력한다.
5. 사용자 지정 제공사에는 다음을 입력한다. 아래 API주소는 실제 외부 HTTPS 주소로 바꿔야 한다.

| Supabase 제공사 항목 | 입력 |
|---|---|
| Identifier | custom:naver |
| Client ID / Client Secret | 위 네이버 앱의 값. Supabase 설정에도 직접 입력 |
| Authorization URL | https://API주소/api/auth/naver/authorize |
| Token URL | https://API주소/api/auth/naver/token |
| UserInfo URL | https://API주소/api/auth/naver/userinfo |
| PKCE | 켜기. S256 사용 |
| Email optional | 켜기 |
| Scopes | 불필요한 추가 범위를 요청하지 않음 |

6. 네이버 프로필에서는 계정의 고유ID와 선택한 닉네임을 사용한다. 임의 이메일 인증이나 이메일만으로 계정 합치기를 하지 않는다.
7. 제공사를 활성화하고 backend/.env의 `AUTH_NAVER_ENABLED=true`로 바꾼다.

네이버 연결의 일회용 state는 현재 FastAPI 한 프로세스의 메모리에서10분 보관한다. 재시작하면 진행 중 연결은 다시 시작해야 한다. **여러 작업 프로세스/서버로 배포하기 전에는 Redis 등 공유 저장소로 바꾸고 호출 제한을 검증해야 한다.** 실제 네이버↔Supabase 인증서버 조합은 아직 검증하지 않았다.

## 5. 로그인 보호 켜기

연동할 제공사만 먼저 켤 수 있다. 예를 들어 Google만 준비됐다면 나머지 두 제공사는 false로 둔다.

1. 저장소의 DB 변경은 `20261103_0006`까지 적용했다. 분석 소유자 항목을 추가했고, Supabase Data API가 공개용 키로 분석·선수·슛·작업 테이블을 직접 읽지 못하도록 RLS를 켰다. 기존 기록을 임의 계정에 배정하거나 삭제하지 않았다.
2. 다른 PC/새 DB에서는 backend 폴더에서 `.venv\Scripts\python.exe -m alembic upgrade head`를 먼저 실행한다.
3. 위 값과 제공사 설정이 준비되면 `AUTH_REQUIRED=true`로 변경하고 백엔드를 다시 실행한다. 프론트도 새로고침한다. **이번 작업에서는 현재 백엔드를 종료·재시작하지 않았다.**
4. 현재 웹 서버가 이전 코드로 실행 중이면 인증 API가 없으므로 개발 화면에는 준비 상태만 나온다. 빌드·DB 준비와 실제 실행 반영을 구분한다.
5. 로그인 보호를 켜면 비로그인 접근은 로그인 화면으로 이동한다. 서버도 세션을 Supabase에서 검증하고 목록·원본 영상·슛·월별 결과·수정·삭제를 소유자별로 제한한다.

소유자가 없는 기존 로컬 기록은 자동으로 새 사용자에게 넘기지 않는다. 로그인 전환 후 필요하면 본인 소유 여부를 확인하고 별도 이전해야 한다. 서로 다른 제공사의 계정을 자동으로 같은 계정으로 합치지 않는다.

## 6. 실제로 확인할 순서

1. 이메일 가입 → 메일 인증 → 로그인 → 새로고침 → 로그아웃.
2. Google → 카카오 → 네이버 각각 로그인과 동의 취소·돌아오기.
3. 다른 계정2개로 목록·월별 집계·영상·개별 결과가 서로 보이지 않는지 확인.
4. 계정 A가 만든 시험 영상을 계정 B에서 직접 주소로 열거나 수정·삭제할 수 없는지 확인.
5. 로그인 만료·연결 실패·여러 탭 로그아웃·원본 영상 구간 반복 재생 확인.

## 구현·검증 범위

- 이메일 비밀번호 가입/로그인, PKCE 콜백, 세 제공사 요청, HTTP 전용 세션 쿠키, 로그아웃, 로그인 후 내부 화면 복귀를 준비했다.
- Supabase의 사용자 조회 API로 서버에서 토큰을 검증한다. 요청의 사용자ID나 검증되지 않은 JWT payload를 사용자 신원으로 신뢰하지 않는다. 비밀번호는 앱 DB에 저장하지 않는다.
- 쿠키로 수정·삭제할 때 Origin을 확인한다. 네이버는 Client Secret·S256·만료·재사용 방지·고정 돌아올 주소를 검사한다. 정해진 제공사 URL만 서버가 호출한다.
- 백엔드223개 시험, 프론트18개 시험·빌드 확인. 제공사 응답은 자동시험에서 모의 응답을 사용했다. 실제 계정 생성·실제 OAuth 성공으로 표현하지 않는다.
- 최초 구현 당시 제공사 앱·공개 키는 미설정이었다. 당시 실제 로그인 요청·메일 발송·유료 AI 분석·기록 삭제는 하지 않았다. 이후 연결 상태는 아래 후속 기록을 따른다.
- Chrome에서 로그인↔회원가입 전환, 이메일·비밀번호·비밀번호 확인 입력란, 비밀번호 보기, 준비 상태와 비활성 버튼을 확인했다. 최종 로그인 화면 캡처는 backend/work/verification-2026-11-03/auth/login.jpg에 보관했다.
- 최종 확인에서 DB 버전20261103_0006·owner_id 항목·앱 테이블5개의 RLS 활성화를 조회했다. 실행 중 API는 기존 분석 조회200·새 인증 설정404로, 백엔드 재시작 전 상태다. 프론트 빌드는 통과했고 큰 파일 묶음에 대한 크기 경고가 남는다. 프론트 시험은 `node --test tests/*.test.mjs`로18개 통과했다.

### Supabase 설정 입력 확인 · 2026-11-03 후속

- 사용자가 입력한 공개 키는 publishable 형식이었다. URL에는 프로젝트 API 주소 대신 관리 화면 주소가 들어 있어 기존 DB의 프로젝트 식별자와 대조한 뒤 프로젝트 루트 HTTPS 주소로 해당 한 항목만 수정했다. 실제 URL·키·DB 비밀값은 이 문서에 적지 않는다.
- 수정한 프로젝트와 공개 키로 Supabase Auth 설정을 읽어 HTTP200을 확인했고 앱 설정도 정상으로 읽혔다. 실제 계정 생성·메일·로그인은 실행하지 않았다.
- Google 제공사는 아직 꺼져 있고 AUTH_REQUIRED=false를 유지한다. Google 앱·제공사 설정 후 로그인 보호를 켜고 백엔드를 재시작하는 순서가 남아 있다. Site URL과 Redirect URL의 대시보드 저장 상태는 이 API 확인만으로 검증하지 않았다.

## Google 설정 반영과 로컬 실행 확인 · 2026-11-03 후속

- 사용자가 Google 설정 완료를 알린 뒤 Supabase의 제공사 설정 조회200·Google 활성화를 확인했다. 공개 키·URL 실제 값은 출력하지 않았다.
- AUTH_REQUIRED와 AUTH_GOOGLE_ENABLED만 true로 바꾸고 네이버·카카오는 false를 유지했다. 실행 중 분석은 없음을 확인한 뒤 기존8000 백엔드만 재시작했다. Vite는 계속 실행 중이며 /api 프록시도 정상이다.
- 백엔드 health·인증 설정200, required/configured/Google 활성화를 확인했다. 로그인 없는 분석 목록 요청은401로 차단됐다. 프론트 로그인 화면과 프록시 인증 설정도200이다.
- Chrome 새로고침 후 이미 로그인된 홈·계정 표시·로그아웃 버튼을 확인했다. 다시 새로고침한 뒤에도 로그인과 홈이 유지됐다. 계정 이메일·토큰·쿠키는 문서에 저장하지 않았다.
- Google 계정 선택·동의 단계는 도구가 직접 수행하지 않았다. 로그아웃·재로그인·다른 계정 접근 분리·로그인 후 새 영상 분석은 아직 후속 검증이다. 기존 기록 변경·유료 AI 호출은 하지 않았다.
- 현재 변경은 미커밋 상태다. 앞선 준비 기록의 ‘백엔드 미재시작·인증 설정404·로그인 보호 꺼짐’은 당시 이력이며 현재 상태는 이 절을 따른다.

## 공식 참고 링크

- [Supabase Google 연결](https://supabase.com/docs/guides/auth/social-sign-in/auth-google)
- [Supabase Kakao 연결](https://supabase.com/docs/guides/auth/social-sign-in/auth-kakao)
- [Supabase 사용자 지정 OAuth 제공사](https://supabase.com/docs/guides/auth/custom-oauth-providers)
- [Supabase PKCE](https://supabase.com/docs/guides/auth/sessions/pkce-flow)
- [네이버 로그인 개발 가이드](https://developers.naver.com/docs/login/devguide)

### 저장 전 최종 확인 · 2026-11-03

사용자의 커밋·푸시 요청으로 로그인 코드·DB 변경·설정 안내와 실행 확인 기록을 저장 대상으로 정리했다. 백엔드223개·프론트18개 시험과 빌드가 통과했다. 기존 린트 경고4개·의존성 경고1개·큰 빌드 파일 경고는 남는다. 실제 .env·계정 식별값·토큰·영상은 저장하지 않는다. Google 계정 선택/동의 전체 과정과 두 실제 계정 분리·로그아웃·재로그인은 별도 검증이다.
