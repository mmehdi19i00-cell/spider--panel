# Spider Panel — Dockerfile (Railway-native)
# Includes automatic official Xray-core installation at build time.
# Also downloads geoip.dat and geosite.dat for proper Xray routing.
#
# Port architecture:
#   * FastAPI (uvicorn) binds the Railway-injected $PORT  -> web dashboard.
#   * Xray binds a SEPARATE internal port (XRAY_INBOUND_PORT, default 24567)
#     and is reached externally only via the Railway TCP proxy port. The two
#     processes NEVER share a port.
#   * The xray binary is installed to /usr/local/bin/xray.
#   * GeoIP and GeoSite databases are installed to /usr/local/bin/

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# --- System deps: wget + unzip + curl (required to fetch Xray) ---
RUN apt-get update \
    && apt-get install -y --no-install-recommends wget unzip curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# --- Install official Xray-core + geoip.dat + geosite.dat to /usr/local/bin/ ---
# Build MUST fail if this step fails (no `|| true`).
RUN set -eux; \
    ARCH="$(dpkg --print-architecture)"; \
    if [ "$ARCH" = "amd64" ]; then XARCH="64"; else XARCH="$ARCH"; fi; \
    cd /tmp; \
    wget -q https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-$XARCH.zip -O xray.zip; \
    unzip -o xray.zip -d /tmp/xray-extracted; \
    install -m 0755 /tmp/xray-extracted/xray /usr/local/bin/xray; \
    # Download geoip.dat and geosite.dat if available in the release
    if [ -f /tmp/xray-extracted/geoip.dat ]; then \
        install -m 0644 /tmp/xray-extracted/geoip.dat /usr/local/bin/geoip.dat; \
    fi; \
    if [ -f /tmp/xray-extracted/geosite.dat ]; then \
        install -m 0644 /tmp/xray-extracted/geosite.dat /usr/local/bin/geosite.dat; \
    fi; \
    # If geo files not in zip, download them separately from the latest release
    if [ ! -f /usr/local/bin/geoip.dat ]; then \
        wget -q https://github.com/XTLS/Xray-core/releases/latest/download/geoip.dat -O /usr/local/bin/geoip.dat || true; \
    fi; \
    if [ ! -f /usr/local/bin/geosite.dat ]; then \
        wget -q https://github.com/XTLS/Xray-core/releases/latest/download/geosite.dat -O /usr/local/bin/geosite.dat || true; \
    fi; \
    rm -rf /tmp/xray.zip /tmp/xray-extracted; \
    /usr/local/bin/xray version; \
    ls -la /usr/local/bin/xray /usr/local/bin/geoip.dat /usr/local/bin/geosite.dat 2>/dev/null || true

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
