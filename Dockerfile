# Spider Panel — Dockerfile (Railway-native)
# Includes automatic official Xray-core installation at build time.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# --- System deps: wget + unzip + curl (required to fetch Xray) ---
RUN apt-get update \
    && apt-get install -y --no-install-recommends wget unzip curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# --- Install official Xray-core (latest stable, Linux 64-bit) ---
# Build MUST fail if this step fails (no `|| true`).
RUN set -eux; \
    mkdir -p /app/xray-core; \
    cd /app/xray-core; \
    wget -q https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip -O xray.zip; \
    unzip -o xray.zip; \
    rm -f xray.zip; \
    chmod +x /app/xray-core/xray; \
    ls -lah /app/xray-core/xray; \
    /app/xray-core/xray version

# --- App deps ---
WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# App expects these at runtime
ENV XRAY_BINARY_PATH=/app/xray-core/xray \
    DATA_DIR=/app/data \
    HOST=0.0.0.0 \
    PORT=8000

RUN mkdir -p /app/data/xray

EXPOSE 8000

# Keep existing FastAPI startup. Xray is spawned by the app itself (process.py).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
