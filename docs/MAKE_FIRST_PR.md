# 초기 업로드용 Git 명령

저장소가 이미 생성되어 있으므로 **빈 저장소로 `git init`하지 않습니다.**

```powershell
cd D:\project
git clone https://github.com/intel7-smart-parking-ai/smart-parking-ai.git
cd .\smart-parking-ai
git switch -c chore/initial-scaffold
# 이 ZIP 파일을 이 폴더에 압축 해제합니다 (기존 README.md/.gitignore 파일은 미포함).
git status
git add .
git commit -m "chore: add initial project scaffold"
git push -u origin chore/initial-scaffold
```

이후 GitHub 저장소에서 `Compare & pull request`로 `main`에 병합 요청을 보냅니다.
