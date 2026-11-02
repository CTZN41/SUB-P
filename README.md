# 농구 슈팅 분석 서비스

한 대의 카메라로 촬영한 농구 영상을 분석해 슈팅 위치와 성공 여부를 기록하고, 슈팅 맵과 히트맵으로 보여주는 로컬 PC용 프로젝트입니다.

현재는 **Phase 10의 결과 요약과 14개 구역 2D 히트맵**까지 완료되어 있습니다. MP4를 분석하면 위치·성공 여부·슛 종류·재생 구간을 Supabase에 저장하고, `/results/{분석번호}` 화면에서 전체·선수별·종류별 통계와 구역별 성공률을 확인할 수 있습니다. 구역을 누르면 해당 슛 목록과 영상 장면으로 연결됩니다. Terra가 불확실한 후보는 Sol이 재검토하며, 성공 장면만 보이지 않으면 슛과 종류는 저장하고 성공 여부만 판단 불가로 남깁니다. 브라우저가 재생하기 어려운 MP4는 원본을 보존한 채 H.264 재생 사본을 자동 준비합니다. 다음 단계는 O/X 성공·실패를 보여주는 Phase 11의 3D 슈팅 맵입니다.

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

실제 슛 후보 분석에는 현재 PC의 `backend/models/basketball-yolo26n-pilot-v4.pt`가 필요합니다. 모델 파일은 Git에서 제외되어 있으므로 다른 PC에서 저장소만 내려받으면 이 기능은 바로 실행되지 않습니다. 기존 v3 파일은 비교용으로 보존합니다.

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

## 긴 영상의 AI 비용 목표

향후 1~1시간 반 연습 영상은 슛 약 100개를 기본 사례로 보고, **한 번 분석할 때 OpenAI API 비용을 약 5천원 이내**로 맞추는 것을 목표로 합니다. 현재 짧은 영상의 사용량을 긴 영상에 단순 적용하면 Terra만 쓸 때 슛 100개는 약 4천~6,500원, 270개는 약 1만~1만 6천원으로 추정됩니다. 실제 비용은 후보 수, 재검사, 환율에 따라 달라집니다.

먼저 AI에 보내는 장면 수를 줄여 정확도를 비교합니다. 슛이 많은 영상은 저렴한 Luna로 쉬운 장면을 판단하고, 애매한 장면만 Terra로 다시 보는 방식을 **사람이 정답을 확인한 영상으로 검증한 뒤** 도입할지 결정합니다. 지금은 Terra 중심이며 Luna 혼합 방식과 1~1시간 반 원본 분석은 구현되지 않았습니다. 현재 등록 가능 길이는 최대 10분입니다.

## 최근 영상 검증

2026-11-02 실내 75초 영상의 별도 새 분석에서 화면으로 확인한 슛 7개와 성공 6·실패 1이 저장 결과와 일치했습니다. 레이업 2개의 구역은 발을 디딘 측면 페인트존 대신 골밑으로 기록돼 위치 검증이 남았습니다. 여러 구도 영상의 후보 시각, 처리 시간, 재현 명령과 정확도 해석 범위는 [Phase 14 측정 기록](docs/phase14-evaluation-2026-11-02.md)을 참고하세요. 이 수치는 전체 서비스의 공식 정확도가 아닙니다.

## 빠른 로컬 후보 탐지 (OpenVINO)

이 PC의 앞 3분 비교는 PyTorch CPU 168.7초, OpenVINO CPU 83.2초, Intel 내장 GPU 170.4초였습니다. 짧은 영상 6개의 후보 11개는 OpenVINO CPU에서도 시각·발 좌표가 같았습니다. 이는 기존 결과 유지 시험이며 모든 영상의 정확도 보장은 아닙니다. [상세 측정과 한계](docs/candidate-performance-review-2026-11-02.md)를 참고하세요.

새 PC에서 기존 `.pt` 모델을 준비한 뒤 백엔드 폴더에서 아래 명령으로 변환합니다. 학습과 OpenAI 호출은 하지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-openvino.txt
.\.venv\Scripts\python.exe evaluation\export_candidate_openvino.py
```

`backend/.env`에 `CANDIDATE_RUNTIME=openvino_cpu`를 설정하고 백엔드를 재실행하면 같은 실행 방식을 사용합니다. `torch`로 기존 방식에 복귀할 수 있습니다. 원본·변환 가중치와 MP4는 로컬 파일이며 Git에는 포함하지 않습니다.

유료 AI와 DB 저장 없이 후보만 시험하려면 백엔드 폴더에서 다음 명령을 실행합니다. 골대 좌표는 이 시험 영상에만 해당합니다.

```powershell
$env:PYTHONPATH = (Get-Location).Path
.\.venv\Scripts\python.exe evaluation\candidate_speed.py C:\Users\aicam\Downloads\IvMhOQO9ros-fixed-camera-repeat-10min-upload.mp4 --seconds 180 --hoop-x 748 --hoop-y 310 --model models\basketball-yolo26n-pilot-v4_openvino_model --pose models\yolo26n-pose_openvino_model --device intel:cpu --output work\performance\manual-180s.json
```

웹 화면의 **AI 포함 전체 분석 (유료)** 버튼은 후보 탐지 뒤 OpenAI 판정까지 진행합니다. 분석 화면에는 실제 경과 시간과 완료 후 후보 탐지 소요 시간을 표시합니다.


### 2026-11-02 분석 정확도·속도 보완

- OpenVINO 후보 탐지에 순차 프레임 읽기와 작업 내 모델 재사용을 적용하고, AI는 최대 두 후보씩 처리합니다. 기본 장면을 유지하면서 림 주변 장면을 보강하고 직접 득점 근거가 부족하면 추가 확인합니다.
- 카메라 구도가 달라져 기존 기준점과 맞지 않는 장면에는 이전 숫자 투영을 적용하지 않습니다. 모호한 경계는 확인 필요로 남습니다.
- [10분 실험 결과](docs/full-ai-trial-result-2026-11-02.md)는 별도 실행 결과이며, localhost의 실제 DB·화면 비교는 진행 중입니다.


### 2026-11-02 localhost 전체 완료·수동 위치·후속 수정

- 실제 AI·DB 전체 분석은20분42.8초, 별도 실험과0.44% 차이였다. 전체 입력2,881,076·출력38,891토큰이다. 반복 후보84/84 대응·판정된 득점80/80 일치이며 독립 정확도는 아니다.
- 57초 뒤 회수 동작 후보를 조건부 분리하고 원본은JSON에 보존한다. 기존 결과 재생에서5개만 분리했다. 509초 후보의511.766초 재탐지가1.8초 제한에 걸린 문제와598초 영상 끝 프레임 부족이 전체AI 실패가 되던 문제를 수정했다. 기존DB는 자동 변경하지 않았다.
- 슛 편집에2D 위치 클릭을 추가했다. Chrome 클릭·좌표·키보드·범위 제한 확인. 실제 저장은 누르지 않았다. 백엔드176개·프론트 빌드 통과. 새 유료 전체 재분석은 하지 않았다. 후속 백엔드 수정은 서버를 다시 시작해야 반영되며 이번 마무리에서 서버를 종료하지 않았다.
- 상세 결과: [localhost 비교 결과](docs/localhost-speed-comparison-2026-11-02.md)
