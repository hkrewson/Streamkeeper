FROM python:3.12-slim

ARG APP_VERSION=0.1.0
LABEL org.opencontainers.image.title="Streamkeeper" \
      org.opencontainers.image.version="$APP_VERSION" \
      org.opencontainers.image.description="Read-only media scanner and compatibility planner" \
      org.opencontainers.image.source="https://github.com/hkrewson/streamkeeper"

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 1026 streamkeeper \
    && mkdir -p /data \
    && chown -R streamkeeper:streamkeeper /data

USER streamkeeper
ENV PORT=8080 \
    STREAMKEEPER_DB=/data/streamkeeper.sqlite3 \
    PYTHONUNBUFFERED=1
EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD curl -fsS http://127.0.0.1:8080/health || exit 1
CMD ["uvicorn", "streamkeeper.web:app", "--host", "0.0.0.0", "--port", "8080"]
