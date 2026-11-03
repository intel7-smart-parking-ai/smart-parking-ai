FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend /app/backend

FROM base AS test
COPY backend/requirements-dev.txt /app/backend/requirements-dev.txt
RUN pip install --no-cache-dir -r backend/requirements-dev.txt
COPY tests /app/tests
CMD ["python", "-m", "pytest", "tests/", "-q"]

FROM base AS production
RUN useradd --create-home --uid 10001 parking
USER parking
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
