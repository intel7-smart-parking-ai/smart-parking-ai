FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements-runtime.lock /app/requirements-runtime.lock
RUN pip install --no-cache-dir -r /app/requirements-runtime.lock
COPY backend /app/backend
RUN useradd --create-home --uid 10001 parking && mkdir /data && chown parking /data
ENV PARKING_MODE=demo SMART_PARKING_DB_PATH=/data/smart_parking.db API_HOST=0.0.0.0 API_PORT=8000
USER parking
EXPOSE 8000
CMD ["python", "-m", "backend.app.main"]

FROM runtime AS test
USER root
COPY backend/requirements-test.lock /app/requirements-test.lock
RUN pip install --no-cache-dir -r /app/requirements-test.lock
COPY tests /app/tests
USER parking
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/"]
