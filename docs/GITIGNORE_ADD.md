# 기존 `.gitignore`에 추가할 추천 내용

저장소에 이미 있는 `.gitignore`를 덮어쓰지 말고 다음 항목을 **추가**하세요.

```gitignore
.env
.env.*
!.env.example
.venv/
node_modules/
frontend/dist/
*.db
*.sqlite
*.sqlite3
configs/parking_a.json
configs/parking_b.json
datasets/
recordings/
models/weights/
*.pt
*.pth
*.onnx
*.bag
__pycache__/
.pytest_cache/
```

데이터셋/학습 가중치는 Git LFS 또는 공유 드라이브 등 별도 저장소 사용을 검토하세요.
