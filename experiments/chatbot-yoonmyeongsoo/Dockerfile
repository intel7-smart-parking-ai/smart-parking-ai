# Python 3.10 경량 베이스 이미지
FROM python:3.10-slim

WORKDIR /app

# 시스템 의존성 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 파이썬 패키지 의존성 복사 및 설치
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 애플리케이션 및 정적 프론트엔드 복사
COPY backend/ /app/backend/
COPY frontend/ /app/frontend/
COPY run.py /app/

ENV PYTHONUNBUFFERED=1
ENV PORT=8080

EXPOSE 8080

# 챗봇 서버 실행
CMD ["python", "run.py"]

