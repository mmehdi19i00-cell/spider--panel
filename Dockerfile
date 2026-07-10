# Spider Gateway - Dockerfile for Railway Deployment
# Proper multi-stage build: Builder installs Xray, Runtime only copies binary

# ============================================================
# BUILDER STAGE: Install Xray Core and Python dependencies
# ============================================================
FROM python:3.11-slim AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    unzip \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies to user site-packages
RUN pip install --no-cache-dir --user -r requirements.txt

# Download, verify and install Xray Core
RUN mkdir -p /tmp/xray-build \
    && wget -q -O /tmp/xray-build/Xray-linux-64.zip \
       "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip" \
    && unzip -q /tmp/xray-build/Xray-linux-64.zip -d /tmp/xray-build/extracted \
    && chmod +x /tmp/xray-build/extracted/xray \
    && /tmp/xray-build/extracted/xray version \
    && rm -rf /tmp/xray-build/Xray-linux-64.zip

# ============================================================
# RUNTIME STAGE: Minimal image with only what's needed
# ============================================================
FROM python:3.11-slim

# Install ONLY runtime dependencies (no wget, no unzip)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Set working directory
WORKDIR /app

# Copy Python packages from builder (as root)
COPY --from=builder /root/.local /home/appuser/.local

# Copy Xray binary from builder
COPY --from=builder /tmp/xray-build/extracted/xray /app/xray-core/xray

# Create ALL required directories BEFORE switching user (as root)
RUN mkdir -p /app/xray-core /app/xray-assets /app/xray-config /app/xray-logs /data

# Copy geo assets from builder IF present (optional - do not fail the build if absent)
COPY --from=builder /tmp/xray-build/extracted/geoip.dat /app/xray-assets/geoip.dat
COPY --from=builder /tmp/xray-build/extracted/geosite.dat /app/xray-assets/geosite.dat

# Set permissions on Xray binary and ALL directories (as root)
RUN chmod +x /app/xray-core/xray \
    && chown -R appuser:appuser \
       /app/xray-core \
       /app/xray-assets \
       /app/xray-config \
       /app/xray-logs \
       /data \
    && /app/xray-core/xray version

# Copy application code with correct ownership
COPY --chown=appuser:appuser . .

# NOW switch to non-root user (all root operations done)
USER appuser

# Add user site-packages to PATH
ENV PATH="/home/appuser/.local/bin:${PATH}"

# Environment variables (NO Railway runtime vars at build time)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    XRAY_BINARY_PATH=/app/xray-core/xray \
    XRAY_CONFIG_PATH=/app/xray-config/config.json \
    XRAY_ASSETS_DIR=/app/xray-assets \
    XRAY_LOG_DIR=/app/xray-logs

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health', timeout=5)" || exit 1

# Start command
CMD ["python", "main.py"]