FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    TZ=Europe/Rome \
    DATA_DIR=/data

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.9.7

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini entrypoint.sh ./
COPY migrations ./migrations
COPY app ./app

# UID 1000 = deployer sull'host: il bind mount ./data:/data è scrivibile
# senza chown e i file restano leggibili dall'operatore.
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin app \
    && chmod +x /app/entrypoint.sh \
    && mkdir -p /data \
    && chown -R app:app /app /data

USER app
ENV PATH="/app/.venv/bin:$PATH"

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD ["python", "-m", "app.healthcheck"]

ENTRYPOINT ["/app/entrypoint.sh"]
