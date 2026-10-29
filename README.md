# 농구 슈팅 분석 서비스

한 대의 카메라로 촬영한 농구 영상을 분석해 슈팅 위치와 성공 여부를 기록하고, 슈팅 맵과 히트맵으로 보여주는 로컬 PC용 프로젝트입니다.

현재는 **Phase 8의 슛 결과 저장과 OpenAI 보조 판정**까지 완료되어 있습니다. MP4를 등록하고 코트 기준점과 선수를 확인한 뒤 분석을 시작하면, PC에서 찾은 슛 후보를 바탕으로 위치·성공 여부·슛 종류·재생 구간을 Supabase에 저장합니다. 불확실하거나 OpenAI 판정에 실패한 후보도 삭제하지 않고 `확인 필요`로 남깁니다. 다음 단계는 사용자가 결과를 검토·수정하고 해당 장면을 재생하는 Phase 9입니다.

## 기술 구성

개발 기준은 **FastAPI + React·TypeScript + Supabase PostgreSQL**입니다. 백엔드는 `backend/.env`의 Supabase Session pooler 연결 문자열을 사용합니다. 설정이 없거나 잘못되면 SQLite로 자동 전환하지 않습니다. 기존 SQLite 파일과 데이터는 보존합니다.

## 한 번에 실행

백엔드와 프론트엔드를 한 PowerShell 창에서 함께 실행할 수 있습니다.

```powershell
Set-Location C:\Users\aicam\Desktop\New
npm run dev
```

종료할 때는 같은 창에서 `Ctrl+C`를 누릅니다.

## 폴더 구성

- `frontend`: React 화면
- `backend`: FastAPI API와 Supabase PostgreSQL 연결 코드
- `docs`: MVP, 요구사항, 동작 설명서, 개발 계획
- `backend/uploads`: 사용자가 등록한 원본 영상 보관 위치
- `backend/work`: 슛 후보 결과와 분석 과정 파일 보관 위치
- `backend/data`: 전환 전에 사용한 SQLite 데이터베이스 보관 위치

실제 슛 후보 분석에는 현재 PC의 `backend/models/basketball-yolo26n-pilot-v3.pt`가 필요합니다. 모델 파일은 Git에서 제외되어 있으므로 다른 PC에서 저장소만 내려받으면 이 기능은 바로 실행되지 않습니다.

## 백엔드 실행

PowerShell에서 아래 명령을 실행합니다.

```powershell
Set-Location C:\Users\aicam\Desktop\New\backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

상태 확인 주소: <http://127.0.0.1:8000/api/health>

처음 내려받은 뒤 데이터베이스 테이블을 준비할 때는 백엔드 폴더에서 다음 명령을 한 번 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

실행 전에 `backend/.env.example`을 참고해 `backend/.env`의 `DATABASE_URL`에 Supabase **Session pooler** 연결 문자열을 입력해야 합니다. 비밀번호와 전체 연결 문자열은 Git이나 문서에 기록하지 않습니다. `alembic upgrade head`는 아직 적용하지 않은 변경만 순서대로 실행합니다.

## 프론트엔드 실행

다른 PowerShell 창에서 아래 명령을 실행합니다.

```powershell
Set-Location C:\Users\aicam\Desktop\New\frontend
npm run dev
```

브라우저 주소: <http://127.0.0.1:5173>

## 비밀정보와 로컬 데이터

실제 API 키와 데이터베이스 연결 문자열은 `backend/.env`에만 입력합니다. `.env`, 업로드 영상, 분석 임시 파일, 기존 SQLite 파일은 Git에 포함되지 않도록 설정했습니다.
