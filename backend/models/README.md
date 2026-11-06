# ShotLog 복구 모델 · 2026-11-06

실제 서비스에서 검증한 가중치와 OpenVINO 실행본을 Git에 함께 보관한다. 개별 파일은 최대 약12MB이며 일반 Git 파일로 저장한다. 영상·데이터셋·임시 학습 결과 전체를 이 폴더에 추가하지 않는다.

| 파일 | 용도 |
|---|---|
| `basketball-yolo26n-pilot-v4.pt` | 복구한 사람·농구공·림 3개 클래스의 농구 탐지 모델 |
| `basketball-yolo26n-pilot-v4_openvino_model/` | 같은 가중치의 동적 입력·FP32 OpenVINO 실행본 |
| `yolo26n-pose.pt` · `yolo26n-pose_openvino_model/` | 공식 포즈 모델과 해당 OpenVINO 실행본 |
| `yolo26n.pt` | 당시 학습의 출발점인 공식 80개 클래스 기초 모델 |
| `manifest.json` | 각 파일의 크기·SHA-256, 버전·변환 조건·출처 |

현재 검증한 실행 방식은 `CANDIDATE_RUNTIME=openvino_cpu`다. 백엔드 의존성과 `requirements-openvino.txt`를 설치하면 이미 변환된 실행본을 사용할 수 있다. PyTorch 방식은 `CANDIDATE_RUNTIME=torch`로 선택한다. `evaluation/export_candidate_openvino.py`는 필요할 때 다시 변환하는 도구이며, 체크아웃 후 매번 변환할 필요는 없다.

농구 모델은 이전 실제 명령으로 데이터201→234→243장, 학습5/5/3/3회를 재현한 새 가중치다. 원본 파일과 바이트가 같은 복구는 아니다. 내부41장 mAP50 전체0.8810·공0.7645와 제한된 별도 영상 시험 결과를 일반 정확도로 표현하지 않는다. 실제 새 두 영상은 2슛(성공1/실패1)·6슛(성공5/실패1), 위치·결과 보류0으로 저장됐다.

기초·포즈 모델은 [공식 Ultralytics v8.4.0 모델 배포](https://github.com/ultralytics/assets/releases/tag/v8.4.0)에서 받았다. 모델의 Ultralytics 출처·라이선스 메타데이터는 유지한다. 학습·변환 조건과 검증 범위는 [복구 보고서](../../docs/model-recovery-2026-11-06.md)에 기록한다.

실제 `.env`는 Git에 포함하지 않는다. `backend/.env.example`을 복사한 뒤 본인의 연결 주소·키·비밀번호를 로컬에서 채운다. 이 모델 폴더만 복구해도 DB와 원본 영상이 자동 복구되는 것은 아니다.
