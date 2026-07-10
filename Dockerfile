FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates unzip jq \
    && rm -rf /var/lib/apt/lists/*

ARG XRAY_ARCH=64
ARG XRAY_VERSION=latest

RUN set -eux; \
    if [ "$XRAY_VERSION" = "latest" ]; then \
      XRAY_VERSION=$(curl -fsSL https://api.github.com/repos/XTLS/Xray-core/releases/latest | jq -r .tag_name); \
    fi; \
    URL="https://github.com/XTLS/Xray-core/releases/download/${XRAY_VERSION}/Xray-linux-${XRAY_ARCH}.zip"; \
    curl -fsSL "$URL" -o /tmp/xray.zip; \
    mkdir -p /opt/xray && \
    cd /opt/xray && \
    unzip -o /tmp/xray.zip && \
    chmod +x xray

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /opt/xray /usr/local/bin

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# کپی کل پروژه
COPY . .

RUN mkdir -p /app/data/xray

ENV PORT=8000 \
    XRAY_BINARY_PATH=/usr/local/bin/xray \
    DATA_DIR=/app/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
CMD curl -fsS http://127.0.0.1:${PORT:-8000}/api/healthz || exit 1

CMD ["sh","-c","uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
