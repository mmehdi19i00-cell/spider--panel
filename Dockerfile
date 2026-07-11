# Spider Panel — Dockerfile (Railway-native)
# Includes automatic official Xray-core installation at build time.
#
# Port architecture:
#   * FastAPI (uvicorn) binds the Railway-injected $PORT  -> web dashboard.
#   * Xray binds a SEPARATE internal port (XRAY_INBOUND_PORT, default 24567)
#     and is reached externally only via the Railway TCP proxy port. The two
#     processes NEVER share a port.
#   * The xray binary is installed to /usr/local/bin/xray.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# --- System deps: wget + unzip + curl (required to fetch Xray) ---
RUN apt-get update \
    && apt-get install -y --no-install-recommends wget unzip curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# --- Install official Xray-core to /usr/local/bin/xray ---
# Build MUST fail if this step fails (no `|| true`).
RUN set -eux; \
    ARCH="$(dpkg --print-architecture)"; \
    if [ "$ARCH" = "amd64" ]; then XARCH="64"; else XARCH="$ARCH"; fi; \
    cd /tmp; \
    wget -q https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-$XARCH.zip -O xray.zip; \
    unzip -o xray.zip -d /tmp/xray-extracted; \
    install -m 0755 /tmp/xray-extracted/xray /usr/local/bin/xray; \
    rm -rf /tmp/xray.zip /tmp/xray-extracted; \
    /usr/local/bin/xray version

# --- App deps ---
WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# App expects these at runtime
ENV XRAY_BINARY_PATH=/usr/local/bin/xray \
    DATA_DIR=/app/data \
    HOST=0.0.0.0

RUN mkdir -p /app/data/xray

# Railway injects $PORT for the web (FastAPI) service. Xray uses its own
# internal port (XRAY_INBOUND_PORT, default 24567) — never $PORT.
EXPOSE 8000

# Start command: FastAPI ALWAYS binds Railway's $PORT. Xray is spawned by
# the app itself (process.py) on its separate internal port.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
