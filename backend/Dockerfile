FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements-runtime.lock /app/requirements-runtime.lock
RUN pip install --no-cache-dir -r /app/requirements-runtime.lock
COPY backend ./backend
COPY tests ./tests
ENV PARKING_RUNTIME=integrated SMART_PARKING_DB_PATH=/data/smart_parking.db
RUN useradd -u 10001 -m parkon && mkdir /data && chown parkon /data
USER parkon
CMD ["uvicorn","backend.app.main:app","--host","0.0.0.0","--port","8000"]
