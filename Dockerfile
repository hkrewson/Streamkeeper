FROM python:3.12-slim

ARG APP_VERSION=0.1.0
ARG TARGETARCH
ARG DOVI_TOOL_VERSION=2.3.4
ARG DOVI_TOOL_AMD64_SHA256=1844258e13c26607b32224bf1fa82b595d3b35949f5467405fda560daad32b3f
ARG DOVI_TOOL_ARM64_SHA256=b4f22a7db56954efe4ed8d02276d0f991799e84602f3584711be6c9df950cb15
LABEL org.opencontainers.image.title="Streamkeeper" \
      org.opencontainers.image.version="$APP_VERSION" \
      org.opencontainers.image.description="Read-only media scanner and compatibility planner" \
      org.opencontainers.image.source="https://github.com/hkrewson/streamkeeper" \
      io.streamkeeper.dovi-tool.version="$DOVI_TOOL_VERSION"

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

RUN case "$TARGETARCH" in \
        amd64) dovi_arch="x86_64"; dovi_sha="$DOVI_TOOL_AMD64_SHA256" ;; \
        arm64) dovi_arch="aarch64"; dovi_sha="$DOVI_TOOL_ARM64_SHA256" ;; \
        *) echo "Unsupported architecture for dovi_tool: $TARGETARCH" >&2; exit 1 ;; \
    esac \
    && dovi_archive="dovi_tool-${DOVI_TOOL_VERSION}-${dovi_arch}-unknown-linux-musl.tar.gz" \
    && curl --fail --location --silent --show-error \
        "https://github.com/quietvoid/dovi_tool/releases/download/${DOVI_TOOL_VERSION}/${dovi_archive}" \
        --output "/tmp/${dovi_archive}" \
    && echo "${dovi_sha}  /tmp/${dovi_archive}" | sha256sum --check --strict \
    && tar --extract --gzip --file "/tmp/${dovi_archive}" --directory /usr/local/bin ./dovi_tool \
    && chmod 0755 /usr/local/bin/dovi_tool \
    && dovi_tool --version \
    && rm -f "/tmp/${dovi_archive}"

WORKDIR /app
COPY pyproject.toml README.md ./
COPY THIRD_PARTY_NOTICES.md /licenses/streamkeeper/THIRD_PARTY_NOTICES.md
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
