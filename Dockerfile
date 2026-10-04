# language: Dockerfile, file: Dockerfile, runtime: Python 3.12
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY pyproject.toml README.md ./
COPY token_abuse_engine ./token_abuse_engine
RUN python -m pip wheel --wheel-dir /wheels ".[redis]"

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/home/app/.local/bin:$PATH

RUN groupadd --system app && useradd --system --gid app --home-dir /home/app --create-home app

WORKDIR /app
COPY --from=builder /wheels /wheels
RUN python -m pip install /wheels/* \
    && rm -rf /wheels
COPY --chown=app:app config.production.yaml ./config.production.yaml

USER app
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"

CMD ["python", "-m", "token_abuse_engine.main", "serve", "/app/config.production.yaml", "--host", "0.0.0.0", "--port", "8080"]
