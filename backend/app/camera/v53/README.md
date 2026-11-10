# V53 36차 · 두 카메라 실시간 주차 관제

Intel D435 두 대로 개발원 A 12칸과 부산역 B 16칸을 읽는다. 차량의 스티커에서 번호와 차종을 판독하고, 차량 지붕의 붉은 LED 위치를 도면에 대응시킨다. 일반칸은 세 종류 모두 정상이고 전기차·장애인 전용칸은 해당 종류만 정상이다. 번호가 불분명하면 빈 값으로 보낸다. 가림이나 연결 단절로 바닥을 확인할 수 없으면 빈칸 전송을 멈춘다.

## 카메라 PC 설치 5줄 (시연 PC 재설치용)

`backend/app/camera/v53` 폴더에서 PowerShell로 실행한다. 모델·도면·빈 바닥 기준은 팀 보관 자료에서 복원해 `assets.local.json`에 경로를 적는다(Git에 없음).

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-v53.txt
Copy-Item assets.example.json assets.local.json   # 이 PC의 모델·도면 경로로 수정
$env:API_BASE_URL = "http://<시연 서버 IP>"; $env:VISION_TOKEN = "<팀에서 받은 토큰>"
.\run_windows.bat --start-camera
```

전송 전 `Test-NetConnection <시연 서버 IP> -Port 80`으로 연결을 확인한다. 토큰은 파일에 저장하지 않고 실행 창에서만 설정한다.

**화면을 닫으면 외부 전송이 꺼진다(기본값).** 카메라 화면(8011)의 상태 조회가 30초 넘게 없으면 외부 주소로의 전송을 끄고 화면에 사유·시각을 표시한다. 시연 PC에서 카메라 화면을 닫아도 시연 서버 전송을 유지하려면 실행 창에서 `$env:V53_UNATTENDED_SEND = "1"`을 추가로 설정한다. 이 설정은 이미 승인·전송을 켠 주소에만 적용되며, 끊긴 동안의 관측을 나중에 보내지 않는다.

## 카메라 PC에서 실행

소스 폴더는 ZIP 안의 `smart_parking_v53`, 팀 저장소에서는 `backend/app/camera/v53`이다. 그 폴더를 VS Code로 열고 PowerShell에서 실행한다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-v53.txt
Copy-Item assets.example.json assets.local.json
```

최초 설정 때 팀 보관 자료에서 Geti IR의 XML·BIN 쌍, 점유·번호 분류 모델, 도면, 인쇄번호 표본, LED 특징 모델과 빈 바닥 기준을 복원한다. `assets.local.json`에 이 PC의 자료 경로를 지정한다. 기존 설정 파일은 계속 사용할 수 있다. 모델 종류와 해시는 [모델 명세](MODEL_MANIFEST_V53.json)에 있다. 선택 OCR 작업자는 [전용 의존성](requirements-ocr.txt)과 `ocr_python`, `ocr_models` 설정을 사용한다.

```powershell
.\run_windows.bat --start-camera
```

[카메라 화면](http://127.0.0.1:8011/)에서 각 도면의 실제 칸 위치를 확인한다. F5로 시작, Esc로 정지, Space로 현재 결과를 저장한다. 로컬 수집 기록은 프로그램 폴더의 `runtime/collection.db`에 저장된다. 저장 사진 분석은 카메라 송신과 분리된다.

## 수신 서버와 관제

팀 저장소 루트에서 서버 가상환경에 `backend/requirements.txt`를 설치한다. 시험용 토큰을 서버와 카메라 프로세스의 `VISION_TOKEN`에 동일하게 설정하고, 카메라의 `API_BASE_URL`에 수신기의 기본 주소를 지정한다. 아래 예시는 같은 PC에 별도 시험 DB를 만든다.

```powershell
$env:PARKING_MODE = "real"
$env:SMART_PARKING_DB_PATH = Join-Path $PWD "camtest.db"
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

카메라에서 관측 전송을 켠 뒤 팀 관리자 화면과 `GET /api/v1/parking-lots/{A,B}/spaces`에서 칸 상태를 확인한다. 교육장 Docker 연결에는 승인된 웹 포트가 필요하다.

## 보내는 이벤트 (팀 API 계약 그대로)

| event_type | 언제 |
| --- | --- |
| `vehicle_observed` | 칸 점유 확인. `car_id`는 D-54 번호 또는 `null` |
| `space_empty_observed` | 검수한 빈 바닥이 현재 프레임에서 확인된 칸만 |
| `mock_fire_led_observed` | 빨간 LED 점등(절대 규칙 6, **모의 화재**). 차량 지붕 위면 `space_id`·`car_id`·`vehicle_type`을 채우고, 통로·칸 경계면 `space_id`·`car_id`가 `null` |

본문 필드는 `event_id, event_type, source, lot_id, camera_id, space_id, car_id, vehicle_type, confidence, bbox_xyxy, observed_at, frame_id`뿐이다. 카메라 내부 표시(`_`로 시작하는 키)는 전송 직전에 제거한다. 화면 표기는 **(모의 화재)** 이며 LED 소등은 표시하지 않는다.

**칸별 번호 고정(36차)**: 점유 중인 칸에서 처음 확정한 번호를 그 칸에 고정한다. 같은 칸이 점유된 채 다른 번호가 읽히면 새 번호 대신 `car_id: null`을 보내 활성 세션의 번호가 바뀌는 409를 막는다. 빈칸이 확인되거나 카메라를 다시 시작하면 고정을 푼다. 번호를 못 읽은 프레임에 고정 번호를 대신 채우지 않는다. 나중의 번호 연결·칸 이동은 서버(박해준) 담당이다.

## 시험 파일 이름

`test_roundN.py`는 N차 현장 수정의 회귀 시험이다. 동결 전이라 이름은 그대로 두고 주요 시험 묶음만 적는다.

| 파일 | 시험 묶음 |
| --- | --- |
| round8 | 전송 근거·시작·분할 POST·모델 시작 |
| round10~14 | 안정 전송·전체 칸 집계, 촬영 결합·회전, 일시정지 후 전송, 수신 확인, 화면 조작 |
| round15~17 | 재연결 도면·지붕 인쇄, 두 카메라 번호 충돌·검수 중단, 카메라·번호 연속성·실행기 |
| round18~20 | LED, 수집·주차 규칙, 스티커 억제·느린 도면 검수·붉은 발광체 |
| round22~27 | 현재 스티커·차량 지붕 LED, 실측 바닥, 빈칸 표시, 인쇄 스티커 근거 |
| round29~33 | 변경 칸 우선 전송, 시작 구도, 가림·주기 전송·수신, 신선도·노출 |
| round34~35 | 국소 바닥·인쇄지, 최초 승인 근거·같은 프레임 LED 우선 |
| round36 | 모의 화재 계약(`mock_fire_led_observed`)·칸별 번호 고정 |

```powershell
python -m pytest web_live/tests -q    # 또는 python -m unittest discover -s web_live/tests -t .
```

## 34차에서 수정한 부분

최초 위치 승인 때 사용자가 검토한 프레임의 빈 바닥과 발광 근거를 한 번의 관측으로 보존한다. 이후 다른 프레임의 같은 지점에서 다시 확인해야 전송할 수 있다. 재승인·연결 단절·오래된 프레임·도면 변경에서는 근거를 폐기한다. 동일한 칸과 프레임의 점유와 모의 화재를 전송할 때는 모의 화재 요청이 먼저 도착할 기회를 최대 100ms 주어 화면의 순간적인 소등을 줄였다. 차량 이동·제거와 다른 칸은 이 대기를 적용받지 않는다.

현재 배치의 검사 수치와 미검증 항목은 [검증 결과](VALIDATION.json)에 있다. 재시작 후 28칸이 모이는 시간, 차량을 손으로 움직인 순간부터의 시간, 요금 세션의 최종 입출차는 서로 다른 측정 항목이다.
