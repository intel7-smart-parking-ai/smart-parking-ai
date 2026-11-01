# AI 스마트 주차장 통합관제 시스템 — Git/GitHub 초보자 협업 가이드

> **대상:** Git을 처음 사용하는 6명 전원  
> **프로젝트:** `intel7-smart-parking-ai/smart-parking-ai` (Private)  
> **일정:** 2026년 11월 2일(월) ~ 11월 13일(금)  
> **PM:** 김한별  
> **기준 환경:** Windows 10/11, PowerShell, Git for Windows, GitHub 웹사이트  
> **운영 원칙:** `main` 직접 개발 금지 → 작업 브랜치에서 개발 → PR 검토 → `main` 병합

---

## 0. 딱 1분만 읽는 Git 개념

| 용어 | 간단한 뜻 | 우리 프로젝트에서 |
|---|---|---|
| Repository(저장소) | 전체 프로젝트 코드 보관함 | `smart-parking-ai` |
| Local(로컬) | 내 PC에 있는 코드 | `D:\project\smart-parking-ai` 등 |
| Remote(원격) | GitHub에 있는 코드 | `origin` = 팀 저장소 |
| `main` | 팀이 공동으로 사용하는 통합본 | 테스트된 코드만 병합 |
| Branch(브랜치) | 내 작업을 위한 독립 작업 공간 | `feature/KAN-25-camera-roi` |
| Commit(커밋) | 변경 내용을 저장하는 기록 | '카메라 연결 기능 추가' |
| Push(푸시) | 내 PC의 커밋을 GitHub로 올림 | `git push` |
| Pull(풀) | GitHub의 커밋을 내 PC로 받음 | `git pull` |
| Pull Request(PR) | 내 코드를 `main`에 합쳐 달라는 검토 요청 | 다른 팀원이 리뷰 후 병합 |
| Conflict(충돌) | 같은 부분을 서로 다르게 수정해 Git이 자동으로 못 합침 | 담당자끼리 해결 |

### 한 줄 흐름

```text
GitHub main
   ↓ clone (처음 한 번) / pull (최신화)
내 PC 프로젝트
   ↓ switch -c
작업 브랜치
   ↓ 파일 수정 → add → commit → push
GitHub 작업 브랜치
   ↓ Pull Request → 리뷰 → Merge
GitHub main
```

> **중요:** `git push`는 코드를 GitHub에 올리는 것이지 `main`에 합치는 것이 아니다. **PR 병합**이 별도로 필요하다.

---

## 1. 팀원별 담당 영역 (변경 금지·협업 기준)

| 담당 | 개인별 책임 | AI 모델 공동 개발 |
|---|---|---|
| **김한별 (PM)** | API 규격, 통합 조율, 문서, 모형 제작 일정 | 박해준과 차량 탐지 모델 |
| **박해준** | FastAPI, DB, 입출차 이벤트 | 김한별과 차량 탐지 모델 |
| **우지원** | 카메라 2대 영상 입력, 주차칸 ROI, 위치 매칭 | 김채린과 차량 번호 스티커 인식 |
| **김채린** | 사용자 모바일 웹, 관리자 통합관제 화면 | 우지원과 차량 번호 스티커 인식 |
| **윤명수** | 요금 계산, 모의 결제, 매출 집계 | 서정범과 LED 모의 화재 인식 |
| **서정범** | LLM 챗봇, 음성인식 검토 | 윤명수와 LED 모의 화재 인식 |

- AI 모델 3개는 **2명씩 데이터 수집·라벨링·학습·테스트·개선을 모두 공동 수행**한다.
- 다른 담당자의 폴더를 변경해야 하면 **먼저 Jira 댓글이나 Discord로 합의**한다.
- 카메라 장치를 각 AI 모델이 제각각 다시 열지 않는다. **카메라 입력 모듈은 우지원이 주관**하고, 영상 프레임을 다른 모듈에 전달한다.
- 입출차 확정과 DB 상태 변경은 **박해준의 서버**가 주관한다. 화면과 LLM이 독자적으로 확정하지 않는다.
- 요금·모의 결제·월 매출은 **윤명수의 서버 계산**을 사용한다. 프론트에서 별도 계산식을 만들지 않는다.

### 권장 소유 폴더

```text
smart-parking-ai/
├── backend/
│   ├── app/
│   │   ├── camera/                # 우지원
│   │   ├── roi/                   # 우지원
│   │   ├── vision/
│   │   │   ├── vehicle/           # 김한별 + 박해준
│   │   │   ├── plate/             # 우지원 + 김채린
│   │   │   └── fire/              # 윤명수 + 서정범
│   │   ├── api/                   # 박해준 중심, 공통 인터페이스는 PM 합의
│   │   ├── database/              # 박해준
│   │   ├── parking/               # 박해준
│   │   ├── billing/               # 윤명수
│   │   ├── chatbot/               # 서정범
│   │   └── simulation/            # 서버와 협의
│   └── requirements.txt
├── frontend/src/                 # 김채린
├── configs/                      # 주차장 설정; ROI 변경은 우지원과 합의
├── docs/                         # PM 관리, 모두 문서 수정 제안 가능
├── tests/                        # 각 담당자 테스트
└── .github/                      # 협업 템플릿
```

---

## 2. 명령어에서 **각자 수정해야 할 4가지**

| 항목 | 이 안내서 예시 | 바꿔야 하나? | 설명 |
|---|---|---|---|
| PC 저장 경로 | `D:\project` | **예** | 본인 PC에 있는 폴더로 변경. D드라이브가 없으면 `C:\project` 등 사용 |
| Git 이름 | `YOUR_GITHUB_NAME` | **예** | 본인 GitHub 사용자명 또는 커밋 작성자 이름 |
| Git 이메일 | `you@example.com` | **예** | 본인 GitHub 계정에 등록한 이메일 또는 GitHub 제공 `noreply` 주소 |
| Jira 작업번호 | `KAN-25` | **예** | 실제로 배정된 **Task 번호**. `KAN-25`는 임시 예시 |
| 기능 이름 | `camera-roi` | **예** | 본인의 담당 기능에 맞춰 `billing`, `chatbot` 등으로 변경 |
| 원격 저장소 주소 | `https://github.com/intel7-smart-parking-ai/smart-parking-ai.git` | **아니요** | 6명 모두 동일하게 사용 |
| 원격 이름 | `origin` | **아니요** | 일반적인 clone으로 생성된 원격 별칭 |
| 기본 브랜치 | `main` | **아니요** | 팀 전체 공통 기준 브랜치 |

> **주의:** 복사할 때 `YOUR_GITHUB_NAME`, `you@example.com`, `KAN-25` 같은 예시는 그대로 사용하지 않는다.

---

## 3. PM이 먼저 확인할 것 (첫날 시작 전)

1. **초기 프로젝트 ZIP이 PR을 거쳐 `main`에 병합되었는지 확인한다.** 팀원에게 `clone`을 안내하기 전에 GitHub `main`에 `backend/`, `frontend/`, `docs/` 등이 보여야 한다.
2. GitHub 조직에 초대한 5명이 **초대를 수락**했는지 확인한다.
3. **조직 초대 수락과 비공개 저장소 쓰기 권한은 별개일 수 있다.** 저장소 `Settings → Collaborators and teams`에서 각 팀원이 **Write 권한**을 받았는지 확인한다. 팀(Team)을 만든 뒤 저장소 접근 권한을 Write로 부여해도 된다.
4. PM 본인 외 팀원은 `main`에 직접 push하지 않고 **브랜치 → PR**을 사용한다.
5. 프로젝트 내 공통 API/DB 규격 변경은 **PM + 해당 모듈 소유자** 검토가 필요하다.
6. `docs/`에 역할, 개발 지침서, API 명세를 두고 모든 AI 코딩 도구에 동일하게 전달한다.

---

## 4. 모든 팀원이 **처음 한 번만** 할 일

### 4-1. Git 설치 여부 확인

PowerShell을 열어 다음을 실행한다.

```powershell
git --version
```

- `git version 2.xx.x`가 나오면 **설치된 것**이다.
- `git` 명령을 찾을 수 없으면 Git for Windows를 설치한다: <https://git-scm.com/downloads/win>
- 설치 후 PowerShell을 **닫았다가 다시 열어** 확인한다.

### 4-2. Git 사용자 정보 등록 (PC당 최초 1회)

```powershell
# 아래 두 값은 반드시 본인 정보로 변경!
git config --global user.name "YOUR_GITHUB_NAME"
git config --global user.email "you@example.com"

# 입력 결과 확인
git config --global user.name
git config --global user.email
```

이 정보는 **커밋 작성자 표시**에 사용된다. GitHub 웹 로그인 자체를 대신하지는 않는다.

### 4-3. GitHub 조직 초대 수락 및 저장소 열기

1. 팀원 본인 GitHub 계정으로 로그인한다.
2. 조직 초대 이메일 또는 GitHub 알림에서 초대를 **Accept**한다.
3. 아래 저장소에 접속해 `README.md`가 보이는지 확인한다.

- 저장소: <https://github.com/intel7-smart-parking-ai/smart-parking-ai>

접근할 수 없다면 **PM에게 저장소 접근 권한**을 확인해 달라고 요청한다.

### 4-4. 내 PC에 저장소 복제 (`clone`) — 최초 1회

아래 예시는 **`D:\project`** 폴더에 작업할 경우다. D드라이브가 없으면 적절한 경로로 변경한다.

```powershell
# 작업 폴더 생성 (없어도 오류 없이 생성)
New-Item -ItemType Directory -Path "D:\project" -Force

# 해당 위치로 이동
cd D:\project

# GitHub 저장소 전체 내려받기 (처음 한 번만!)
git clone https://github.com/intel7-smart-parking-ai/smart-parking-ai.git

# 프로젝트 폴더로 이동
cd .\smart-parking-ai

# 현재 상태 확인
git status

# 저장소 주소 확인
git remote -v

# 현재 브랜치 확인
git branch --show-current
```

**정상 결과:** `On branch main`, `nothing to commit, working tree clean`가 표시되고, `origin` URL이 팀 저장소로 나온다.

> **이미 이 폴더가 있다면 `git clone`을 다시 하지 말 것.** `cd D:\project\smart-parking-ai`로 들어가서 작업한다.

### 4-5. VS Code로 열기 (선택)

```powershell
code .
```

`code` 명령이 없다면 VS Code에서 **File → Open Folder → smart-parking-ai**를 선택하면 된다.

---

## 5. 새로운 작업을 시작하는 순서 (가장 중요)

> 아래 예시는 **우지원이 Jira `KAN-25` 카메라/ROI 작업을 맡은 경우**다. 실제 작업번호가 나오면 교체한다.

### 5-1. 프로젝트로 이동하고 상태 확인

```powershell
cd D:\project\smart-parking-ai

git status
```

- `working tree clean`이면 진행해도 된다.
- 변경 사항이 있으면 **무슨 파일인지 확인**한 뒤 커밋 또는 담당자와 상의한다. **수정된 파일을 무작정 삭제/초기화하지 않는다.**

### 5-2. `main`을 최신으로 갱신

```powershell
git switch main
git pull --ff-only origin main
```

- `git switch main`: 현재 작업 위치를 `main`으로 전환
- `git pull --ff-only origin main`: GitHub `main`의 새 커밋을 내 PC로 받음. 자동 병합 충돌이 나면 실패하도록 하는 안전 옵션

### 5-3. 새 작업 브랜치 생성

```powershell
# 실제 Jira Task 번호와 기능 이름으로 변경
git switch -c feature/KAN-25-camera-roi

# 현재 작업 브랜치 확인
git branch --show-current
```

**새 브랜치는 새 작업을 시작할 때만 만들면 된다.** 같은 작업 브랜치에서 매일 다시 `-c`를 쓰면 오류가 난다.

### 5-4. 첫 번째 업로드 시 브랜치 등록 (필요할 때)

```powershell
git push -u origin feature/KAN-25-camera-roi
```

`-u`로 원격 연결을 설정한 뒤에는 같은 브랜치에서 `git push`만 해도 된다. 소스 수정 후 첫 커밋을 만들고 푸시해도 된다.

---

## 6. 코드를 수정한 후 저장하는 순서

### 6-1. **내 작업 브랜치**인지 먼저 확인

```powershell
git branch --show-current
git status
```

`main`이라고 나오면 **일단 멈추고** 현재 상황을 PM에게 확인한다. `main`에서 코드를 계속 수정하지 않는다.

### 6-2. VS Code 등에서 담당 기능 수정 → 실행 테스트

예: 우지원은 `backend/app/camera/`, `backend/app/roi/` 안에서 작업한다. 코드는 **직접 실행해서 오류를 확인**해야 한다.

### 6-3. 변경 사항 확인

```powershell
git status
git diff --stat
```

- `modified`: 기존 파일 수정
- `untracked`: 새로 만든 파일
- `deleted`: 삭제한 파일

### 6-4. 커밋할 파일 선택 (`git add`)

가능하면 **본인이 수정한 파일만** 추가한다.

```powershell
# 예시: 카메라/ROI 소스만 추가
git add backend/app/camera/ backend/app/roi/

# 커밋 대상 확인
git diff --cached --stat
git status
```

`git add .`는 모든 변경 파일을 담으므로 **초보자는 비밀키/데이터/다른 팀 파일을 실수로 넣을 위험**이 있다. 파일 목록을 확인하고 필요한 것만 넣는다.

### 6-5. 커밋 (내 PC에 작업 내용 기록)

```powershell
git commit -m "KAN-25: 카메라 영상 입력 및 ROI 매핑 구현"
```

**커밋 메시지 예시**

- `KAN-25: 카메라 A/B 입력 테스트 추가`
- `KAN-26: 차량 탐지 모델 출력 형식 적용`
- `KAN-27: 주차 세션 저장 API 구현`
- `KAN-28: 모의 결제 중복 방지 테스트`

### 6-6. GitHub에 업로드 (`push`)

```powershell
git push
```

처음 업로드하는 브랜치라면 Git이 `--set-upstream`을 요구할 수 있다. 그럴 때만 아래를 실행한다.

```powershell
git push -u origin feature/KAN-25-camera-roi
```

**기억:** `commit`은 내 PC에 기록, `push`는 GitHub에 업로드다. `main` 병합과는 다르다.

---

## 7. GitHub에서 PR(Pull Request) 생성하기

1. <https://github.com/intel7-smart-parking-ai/smart-parking-ai> 접속.
2. 작업 브랜치를 push하면 표시되는 **Compare & pull request** 클릭. 없으면 **Pull requests → New pull request**.
3. 아래 값을 확인한다.

```text
base:    main                        # 합쳐질 대상
compare: feature/KAN-25-camera-roi   # 내 작업 브랜치
```

4. 변경 파일 목록과 삭제된 파일이 없는지 확인한다.
5. PR 제목과 설명을 작성한다. **리뷰어**로 다른 팀원을 지정한다.
6. **Create pull request** 클릭.
7. 리뷰 의견을 수정해야 하면 **같은 작업 브랜치에서 수정 → add → commit → push**한다. PR은 자동으로 갱신된다.
8. 테스트·리뷰가 끝나면 PM 또는 승인된 담당자가 **Merge pull request → Confirm merge** 한다.
9. GitHub에 **PR 링크**를 Jira Task에 남긴다.

### PR 제목 예시

```text
[KAN-25] A/B 카메라 영상 입력 및 ROI 매핑 구현
```

### PR 설명 양식 (복사해서 작성)

```markdown
## 관련 Jira 작업
KAN-25

## 변경 내용
- 카메라 A/B 영상 입력 처리
- 주차칸 ROI 좌표 로딩 및 매칭

## 변경한 폴더/파일
- backend/app/camera/
- backend/app/roi/

## 테스트 방법 및 결과
- 실행 명령: ...
- 결과: A/B 카메라 프레임 확인 (성공/실패)
- 증거: 스크린샷 또는 실행 로그 첨부

## 공통 API/DB 변경 여부
- 없음 / 있음 (변경 시 PM 및 소유자와 협의)

## 리뷰 요청
- 담당 팀원 / PM
```

> **공동 AI 모델**은 두 사람이 전체 과정을 함께 수행하고, **PR도 공동 검토**한다. PR 기본 작성자는 한 명이어도 괜찮다.

---

## 8. PR 병합 후 내 PC를 최신으로 만들기

**PR이 `main`에 병합된 것을 GitHub에서 확인한 후**:

```powershell
# 작업이 전부 커밋되었는지 먼저 확인
git status

# 통합본(main)으로 이동
git switch main

# GitHub에서 최신 통합본 받기
git pull --ff-only origin main

# 병합된 내 작업 브랜치만 로컬에서 정리 (선택)
git branch -d feature/KAN-25-camera-roi
```

- `git branch -d`는 **내 PC의 브랜치만 삭제**한다. `main` 코드는 삭제되지 않는다.
- 삭제하지 않아도 작업에는 문제없다. **아직 PR이 병합되지 않았다면 삭제하지 않는다.**
- 다음 Jira 작업을 시작할 때 다시 `main`에서 새 브랜치를 만든다.

---

## 9. **2명이 같이 개발하는 AI 모델** 작업법

팀별 공동 모델:

| 모델 | 공동 개발자 | 예시 브랜치 (실제 Jira Task 번호로 변경) |
|---|---|---|
| 차량 탐지 | 김한별 + 박해준 | `feature/KAN-XX-vehicle-ai` |
| 번호 OCR | 우지원 + 김채린 | `feature/KAN-XX-plate-ocr` |
| LED 인식 | 윤명수 + 서정범 | `feature/KAN-XX-fire-led` |

**모델 공동 개발은 데이터 수집, 라벨링, 학습, 테스트, 개선을 반씩 나누지 않는다.** 두 명이 함께 진행하되 Git 변경 이력은 분명히 남긴다.

### 권장 방식 A — 초보자에게 가장 안전

1. Jira Task 하나를 생성한다.
2. 두 명이 작업 범위·수정 파일을 같이 결정한다.
3. 한 명이 기능 브랜치에서 코드를 수정·커밋·푸시한다.
4. 다른 한 명은 코드를 내려받아 실행·검토하고 PR 리뷰에 참여한다.
5. 다음 작업은 역할을 바꾸어 진행할 수 있지만, **모델 개발 자체는 계속 공동 진행**한다.

### 방식 B — **같은 작업 브랜치**를 두 명이 함께 사용

**첫 번째 사람 (이미 원격 브랜치를 만든 사람)**

```powershell
# 최신 코드 확인
git switch feature/KAN-XX-vehicle-ai
git pull --ff-only origin feature/KAN-XX-vehicle-ai

# 수정 → add → commit → push
git push
```

**두 번째 사람 (다른 PC에서 처음 해당 브랜치를 받는 사람)**

```powershell
cd D:\project\smart-parking-ai

git fetch origin

# 원격에서 이미 만들어진 동일한 브랜치를 체크아웃
git switch --track origin/feature/KAN-XX-vehicle-ai

# 작업 시작 전 최신화
git pull --ff-only origin feature/KAN-XX-vehicle-ai
```

이미 로컬에 같은 브랜치가 있다면 `git switch --track`을 다시 하지 않고:

```powershell
git switch feature/KAN-XX-vehicle-ai
git pull --ff-only origin feature/KAN-XX-vehicle-ai
```

**공동 브랜치 필수 규칙**

- 작업 시작 전 항상 `git pull --ff-only origin 브랜치명`.
- **같은 파일의 같은 부분을 동시에 수정하지 않도록** Discord로 작업 시각을 맞춘다.
- 작업 후 바로 커밋·푸시하고 상대에게 알린다.
- 상대의 커밋을 받기 전 로컬 변경 사항을 무리하게 덮어쓰지 않는다.
- 충돌이나 `push rejected`가 나오면 강제 푸시하지 말고 PM에게 로그를 공유한다.

> **중요:** `git pull`을 실행한다고 두 사람이 작성 중인 코드가 실시간 공유되는 것은 아니다. **커밋 → 푸시**된 코드만 다른 PC에서 `pull`로 받을 수 있다.

---

## 10. 매일 사용하는 명령어 — 짧은 버전

### 아침: **새 작업**을 시작한다면

```powershell
cd D:\project\smart-parking-ai
git status
git switch main
git pull --ff-only origin main
git switch -c feature/KAN-25-camera-roi
```

### 아침: **어제 하던 작업**을 이어간다면

```powershell
cd D:\project\smart-parking-ai
git switch feature/KAN-25-camera-roi
git pull --ff-only origin feature/KAN-25-camera-roi
```

> 단, 해당 브랜치를 아직 GitHub에 한 번도 push하지 않았다면 `git pull`은 생략한다.

### 작업 중/퇴근 전

```powershell
git branch --show-current
git status

# 담당 파일만 선택해서 추가
git add backend/app/camera/ backend/app/roi/

git diff --cached --stat
git commit -m "KAN-25: 오늘 작업 내용"
git push
```

`git push` 전에 upstream이 없다면 `git push -u origin feature/KAN-25-camera-roi`로 한 번 연결한다.

### PR 병합 후

```powershell
git switch main
git pull --ff-only origin main
```

---

## 11. 반드시 익혀둘 안전한 확인 명령어

| 명령어 | 의미 | 자주 쓰는 때 |
|---|---|---|
| `git status` | 현재 수정 파일과 브랜치 상태 | **모든 Git 작업 전후** |
| `git branch --show-current` | 내가 작업 중인 브랜치 | 커밋 전 |
| `git branch -a` | 로컬/원격 브랜치 목록 | 브랜치 찾기 |
| `git remote -v` | GitHub 저장소 주소 | 잘못된 저장소인지 확인 |
| `git fetch origin` | 원격 브랜치/커밋 정보만 가져옴 | 팀원이 만든 브랜치를 찾을 때 |
| `git pull --ff-only origin main` | `main` 최신화 | 새 기능 개발 전 |
| `git diff` | 커밋 전 변경 내용 | 코드 검토 |
| `git diff --cached` | 커밋 대상으로 선택한 변경 내용 | `git add` 후 |
| `git log --oneline -5` | 최근 커밋 5개 | 진행 내역 확인 |

### 되도록 사용하지 말아야 할 위험한 명령

| 명령어 | 위험 |
|---|---|
| `git push --force` | 원격의 다른 팀원 커밋을 덮어쓸 수 있음 |
| `git reset --hard` | 내 PC에서 커밋하지 않은 작업을 잃을 수 있음 |
| `git clean -fd` | Git에 등록하지 않은 파일/폴더가 삭제될 수 있음 |
| `git branch -D` | 병합하지 않은 브랜치도 강제로 삭제할 수 있음 |
| `git restore .` | 아직 커밋하지 않은 수정 사항을 되돌릴 수 있음 |

**초보자는 위 명령을 임의 실행하지 말고 PM에게 먼저 확인**한다.

---

## 12. 초보자가 자주 만나는 오류와 해결 순서

### ① `'git' is not recognized` / Git을 찾을 수 없음

- Git for Windows 설치 여부 확인.
- PowerShell을 완전히 닫았다가 다시 실행.
- `git --version` 확인.

### ② `Repository not found` / `403 Permission denied`

- 주소가 `intel7-smart-parking-ai/smart-parking-ai`인지 확인.
- **조직 초대 수락** 여부 확인.
- **Private 저장소 Write 권한** 부여 여부를 PM에게 확인.
- GitHub 인증 창이 나오면 본인 GitHub 계정으로 로그인. GitHub 비밀번호를 터미널에 직접 붙여넣는 방식은 사용하지 않음.

### ③ `fatal: not a git repository`

프로젝트 폴더 안에서 Git 명령을 실행해야 한다.

```powershell
cd D:\project\smart-parking-ai
git status
```

### ④ `Please tell me who you are`

```powershell
git config --global user.name "YOUR_GITHUB_NAME"
git config --global user.email "you@example.com"
```

본인 정보로 변경한다.

### ⑤ `fatal: a branch named ... already exists`

`-c`는 새 브랜치를 만들 때만 사용한다. 이미 있다면:

```powershell
git switch feature/KAN-25-camera-roi
```

### ⑥ `Your local changes would be overwritten by checkout`

**덮어쓰지 말고** 먼저:

```powershell
git status
git diff --stat
```

작업 중이던 파일을 먼저 정상 커밋하거나 PM에게 확인한다. `reset --hard`, `restore .`로 강제로 해결하지 않는다.

### ⑦ `nothing to commit, working tree clean`

오류가 아니다. 현재 Git이 감지한 변경 파일이 없다는 뜻이다. 변경 파일이 `.gitignore`에 의해 제외되거나 다른 폴더에서 작업 중인지 확인한다.

### ⑧ `rejected (non-fast-forward)` / `failed to push some refs`

대개 같은 브랜치에 다른 팀원이 먼저 푸시한 경우다. **강제 푸시는 금지.**

```powershell
git status
git fetch origin
git branch --show-current
```

- **아직 내 로컬 커밋이 없다면** `git pull --ff-only origin 작업브랜치명` 후 작업을 계속한다.
- **이미 내 커밋과 상대 커밋이 모두 생긴 경우** PM과 조율해서 `rebase`/`merge`로 해결한다. 초보자는 무작정 명령을 복사하지 않는다.

### ⑨ 머지 충돌(Conflict)이 났을 때

1. 변경 파일을 백업하거나 현재 상태를 보존한다.
2. VS Code Source Control에서 충돌 파일을 확인한다.
3. 어느 구현을 남길지 **파일 소유자와 합의**한다.
4. 중복 코드를 정리하고 실행 테스트한다.
5. 해결 과정에서 막히면 오류 전체와 `git status` 결과를 PM에게 전달한다.

### ⑩ `.env`, 비밀번호, API 키를 실수로 커밋했다면

**즉시 PM에게 알리고 해당 키를 폐기/재발급한다.** `.gitignore`에 추가만 한다고 이미 업로드된 커밋 기록의 비밀이 사라지는 것은 아니다. GitHub 이력 정리는 PM이 담당한다.

---

## 13. GitHub에 올리지 말아야 할 파일

- `.env` 실제 비밀번호/API 키, 서비스 인증 토큰
- `.venv/`, `venv/`, Python 가상환경
- `node_modules/`, 프론트엔드 설치 패키지 폴더
- `__pycache__/`, `*.pyc`, 임시 캐시
- 대용량 원본 이미지·학습 영상·개인 촬영 데이터
- 대용량 학습 모델 가중치(`.pt`, `.pth`, `.onnx` 등) **팀 합의 없는 직접 커밋 금지**
- 운영용 `.db`, `.sqlite3` 등 실제 DB 파일
- 사용자 개인정보 및 개인 PC 경로가 포함된 비밀 설정 파일

반대로 **`.env.example`(비밀값을 넣지 않은 예시), `requirements.txt`, `package.json`, README, 모델 사용법, 데이터셋 위치 설명은 공유**한다.

> 모델/데이터셋은 별도 보관소를 정하고 **파일명·버전·저장 위치·해시·평가 결과**를 문서로 공유한다. 필요하면 Git LFS 도입을 PM이 결정한다.

---

## 14. PM·리뷰어의 PR 병합 확인표

- [ ] Jira 작업번호와 변경 목적이 적혀 있다.
- [ ] 본인 담당 폴더만 수정했거나 타 담당자 동의가 있다.
- [ ] 실제 실행 테스트에 성공했고 증거가 있다.
- [ ] 공통 API/DB/설정 변경은 해당 담당자가 검토했다.
- [ ] `.env`, 비밀키, 대용량 영상/모델이 포함되지 않았다.
- [ ] 두 명 공동 AI 개발 작업은 두 명 모두 결과를 확인했다.
- [ ] `main` 기준 PR이고 파일 삭제/충돌이 없는지 확인했다.
- [ ] PR 병합 후 `main` 최신 버전의 실행 상태를 확인했다.

> Jira에 `완료(Done)`로 표시하는 시점은 **커밋했을 때가 아니라 실행 검증과 PR 병합이 완료된 뒤**다.

---

## 15. 팀원 6명에게 공유할 최종 체크리스트

**첫날만 수행**

- [ ] Git 설치
- [ ] GitHub 조직 초대 수락
- [ ] Private 저장소 접근·Write 권한 확인
- [ ] 본인 Git 이름/이메일 설정
- [ ] `git clone`으로 코드 내려받기
- [ ] `git status` 정상 확인

**새 작업마다 수행**

- [ ] Jira Task 번호 확인
- [ ] 최신 `main` 받기 (`git pull --ff-only origin main`)
- [ ] 새 기능 브랜치 생성
- [ ] 본인 담당 코드만 수정·실행 테스트
- [ ] `git status`로 변경 확인
- [ ] 필요한 파일만 `git add`
- [ ] `git commit` 후 `git push`
- [ ] GitHub PR 생성 및 리뷰 요청
- [ ] PR 병합 후 Jira 완료 처리

---

## 16. 외부 공식 참고 문서

- [Git 설치/사용 설명서](https://git-scm.com/doc)
- [Git 브랜치 전환 명령 `git switch`](https://git-scm.com/docs/git-switch)
- [GitHub 저장소에서 PR 만들기](https://docs.github.com/en/pull-requests/how-tos/create-pull-requests/creating-a-pull-request)
- [GitHub 조직 저장소의 권한](https://docs.github.com/en/organizations/managing-user-access-to-your-organizations-repositories/managing-repository-roles/repository-roles-for-an-organization)

**문서 관리 책임:** 김한별(PM). 팀 공통 명령어·API·저장소 구조가 바뀌면 이 파일도 함께 수정한다.
