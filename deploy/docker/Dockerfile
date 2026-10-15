# =============================================================================
# AlphaTrader Production Multi-Stage Dockerfile
# Stage 1: Build & wheels generation with compilers
# Stage 2: Hardened, minimal, non-root runtime container
# Governance: ADR-001 (Microservices), ADR-002 (Paper Only Invariant)
# =============================================================================

# --- Stage 1: Builder ---
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .

RUN pip install --no-cache-dir --upgrade pip build wheel setuptools \
    && pip wheel --no-cache-dir --wheel-dir /build/wheels -e .

# --- Stage 2: Hardened Non-Root Runtime ---
FROM python:3.11-slim AS runner

LABEL org.opencontainers.image.title="AlphaTrader Autonomous Multi-Agent Trading System" \
      org.opencontainers.image.description="Institutional-grade autonomous trading platform for NSE NIFTY 50" \
      org.opencontainers.image.authors="AlphaTrader Engineering Team" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create dedicated non-root system user & group (UID 10001)
RUN groupadd -g 10001 alphatrader && \
    useradd -u 10001 -g alphatrader -s /bin/bash -m alphatrader

# Install pre-built wheels from builder
COPY --from=builder /build/wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels

# Copy application source tree and configurations
COPY --chown=alphatrader:alphatrader src/ /app/src/
COPY --chown=alphatrader:alphatrader pyproject.toml /app/pyproject.toml

# Set secure environment defaults
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    TRADING_MODE=PAPER \
    LIVE_TRADING_ENABLED=False

# Healthcheck invoking the deterministic safety validator
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -m src.common.healthcheck || exit 1

# Drop root privileges
USER alphatrader

ENTRYPOINT ["python"]
CMD ["-m", "src.common.healthcheck"]
