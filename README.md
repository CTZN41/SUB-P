# 농구 슈팅 분석 서비스

한 대의 카메라로 촬영한 농구 영상을 분석해 슈팅 위치와 성공 여부를 기록하고, 슈팅 맵과 히트맵으로 보여주는 로컬 PC용 프로젝트입니다.

현재는 **Phase 3 영상 등록과 기본 검사**까지 준비되어 있습니다. MP4 한 개를 등록하면 실제 형식, 최대 10분·5GB, 영상 정보와 필수 동의를 확인하고 원본과 분석 기록을 로컬 PC에 저장합니다.

## 한 번에 실행

백엔드와 프론트엔드를 한 PowerShell 창에서 함께 실행할 수 있습니다.

```powershell
Set-Location C:\Users\aicam\Desktop\bulidlog\New
npm run dev
```

종료할 때는 같은 창에서 `Ctrl+C`를 누릅니다.

## 폴더 구성

- `frontend`: React 화면
- `backend`: FastAPI API와 SQLite 저장소
- `docs`: MVP, 요구사항, 동작 설명서, 개발 계획
- `backend/uploads`: 사용자가 등록한 원본 영상 보관 위치
- `backend/work`: 분석 과정의 임시 파일 보관 위치
- `backend/data`: SQLite 데이터베이스 보관 위치

## 백엔드 실행

PowerShell에서 아래 명령을 실행합니다.

```powershell
Set-Location C:\Users\aicam\Desktop\bulidlog\New\backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

상태 확인 주소: <http://127.0.0.1:8000/api/health>

처음 내려받은 뒤 데이터베이스 테이블을 준비할 때는 백엔드 폴더에서 다음 명령을 한 번 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

## 프론트엔드 실행

다른 PowerShell 창에서 아래 명령을 실행합니다.

```powershell
Set-Location C:\Users\aicam\Desktop\bulidlog\New\frontend
npm run dev
```

브라우저 주소: <http://127.0.0.1:5173>

## 비밀정보와 로컬 데이터

실제 API 키는 `backend/.env`에만 입력합니다. `.env`, 업로드 영상, 분석 임시 파일, SQLite 파일은 Git에 포함되지 않도록 설정했습니다.
