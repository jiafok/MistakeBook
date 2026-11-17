# ---------- Stage 1: build frontend ----------
FROM node:20-alpine AS frontend-build
WORKDIR /build

COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npx vue-tsc --noEmit && npm run build:only

# ---------- Stage 2: runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATABASE_URL=sqlite:////data/mistakebook.db \
    STORAGE_LOCAL_DIR=/data/storage

# Minimal runtime libs. PyMuPDF/Pillow ship manylinux wheels, no build toolchain needed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY database ./database
COPY --from=frontend-build /build/dist ./frontend/dist

RUN mkdir -p /data/storage \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app /data

USER appuser

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/v1/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
