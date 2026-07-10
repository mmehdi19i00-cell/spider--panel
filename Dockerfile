# ===========================================================================
# Spider Panel — Dockerfile (Railway / any OCI runtime)
# Multi-stage: download official Xray-core at build time, then a slim runtime.
# Ports are NOT hardcoded — read from env (PORT for web, XRAY_PORT for xray).
# ===========================================================================
FROM python:3.12-slim AS builder

# Install build deps to fetch the xray binary
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates unzip jq \
    && rm -rf /var/lib/apt/lists/*

# ----- Download latest official Xray-core (linux amd64 by default) -----
# Override XRAY_ARCH at build time for other arches (e.g. arm64 -> arm64-v8a)
ARG XRAY_ARCH=64
ARG XRAY_VERSION=latest
RUN set -eux; \
    if [ "$XRAY_VERSION" = "latest" ]; then \
      XRAY_VERSION=$(curl -fsSL https://api.github.com/repos/XTLS/Xray-core/releases/latest | jq -r .tag_name); \
    fi; \
    echo "Xray version: $XRAY_VERSION"; \
    URL="https://github.com/XTLS/Xray-core/releases/download/${XRAY_VERSION}/Xray-linux-${XRAY_ARCH}.zip"; \
    echo "Downloading $URL"; \
    curl -fsSL "$URL" -o /tmp/xray.zip; \
    mkdir -p /opt/xray && cd /opt/xray && unzip -o /tmp/xray.zip && chmod +x xray; \
    /opt/xray/xray --version || true

# ===========================================================================
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Runtime deps (curl for healthchecks, tzdata for correct times)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the xray binary from builder
COPY --from=builder /opt/xray /usr/local/bin

# Python deps first (cache layer)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# App source
COPY app ./app
COPY scripts ./scripts

# Data path (sqlite + generated xray config live here).
# On Railway, attach a Volume and mount it at /app/data (set DATA_DIR=/app/data).
# Railway does NOT support the VOLUME instruction in Dockerfiles — configure the
# volume in the Railway dashboard instead.
RUN mkdir -p /app/data/xray

# Never bind a hardcoded port: uvicorn listens on $PORT (Railway injects it)
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:${PORT:-8000}/api/healthz || exit 1

ENV PORT=8000 \
    XRAY_BINARY_PATH=/usr/local/bin/xray \
    DATA_DIR=/app/data

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
