# AI 스마트 주차장 — Git 명령어 한 장 요약

**팀 GitHub:** https://github.com/intel7-smart-parking-ai/smart-parking-ai  
**PM:** 김한별 · **기준:** Windows PowerShell  
**중요:** `D:\project`는 본인 PC 경로로, `KAN-25`는 실제 Jira Task 번호로 변경.

## 최초 1회

```powershell
git --version
git config --global user.name "YOUR_GITHUB_NAME"   # 본인 이름으로 변경
git config --global user.email "you@example.com"    # 본인 이메일로 변경
New-Item -ItemType Directory -Path "D:\project" -Force
cd D:\project
git clone https://github.com/intel7-smart-parking-ai/smart-parking-ai.git
cd .\smart-parking-ai
git status
```

이미 clone했다면 다시 하지 말고 `cd D:\project\smart-parking-ai`만 실행한다.

## 새 작업 시작

```powershell
cd D:\project\smart-parking-ai
git status                        # 변경 파일이 남아있으면 먼저 확인
git switch main
git pull --ff-only origin main
git switch -c feature/KAN-25-camera-roi    # 이름과 번호 변경!
```

## 작업 중 / 작업 끝날 때

```powershell
git branch --show-current         # main이면 멈추고 확인
git status
git add backend/app/camera/ backend/app/roi/ # 본인 담당 파일로 변경
git diff --cached --stat
git commit -m "KAN-25: 카메라 기능 구현"  # 번호/내용 변경!
git push -u origin feature/KAN-25-camera-roi  # 최초 1회
# 이후부터는 위 줄 대신 git push 만 실행
```

## 같은 브랜치를 다음 날 계속 사용할 때

```powershell
cd D:\project\smart-parking-ai
git switch feature/KAN-25-camera-roi
git pull --ff-only origin feature/KAN-25-camera-roi
git status
```

## 팀원이 만든 공동 작업 브랜치를 처음 받을 때

```powershell
git fetch origin
git switch --track origin/feature/KAN-25-camera-roi
```

## 작업 결과 합치기

GitHub → **Compare & pull request** → `base: main` / `compare: 내 작업 브랜치` → PR 생성 → 리뷰 → Merge.  
병합 후:

```powershell
git switch main
git pull --ff-only origin main
```

## 자주 쓰는 명령 의미

| 명령 | 뜻 |
|---|---|
| `git status` | 지금 상태 확인 |
| `git switch` | 브랜치 이동 |
| `git switch -c` | 새 브랜치 생성·이동 |
| `git pull` | GitHub 변경 가져오기 |
| `git add` | 커밋할 파일 선택 |
| `git commit` | 변경 기록 저장 |
| `git push` | GitHub 업로드 |
| PR/Merge | 팀 통합본 `main`에 반영 |

**금지:** `main` 직접 개발, `git push --force`, 이유 모르는 `git reset --hard`, 비밀번호/API 키 커밋.

**오류 났을 때:** 더 진행하지 말고 `git status`, 오류 메시지 전체, 현재 브랜치명(`git branch --show-current`)을 PM에게 공유.
