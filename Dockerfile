# M9a K1–K3: one image with the web app, the API and the SRD data. No campaign data, no key.

FROM node:24-bookworm-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS app
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    FASTEMBED_CACHE_PATH=/app/.cache/fastembed \
    PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
COPY scripts ./scripts
RUN uv sync --locked --no-dev

# SRD data at build time (K1): PDF (CC-BY-4.0), chunks, Open5e snapshot, vector index, and both
# embedding models, so start-up needs no network beyond the model provider.
RUN python scripts/download_srd.py \
 && dungeonmind ingest \
 && dungeonmind sync-open5e \
 && dungeonmind index \
 && python -c "from fastembed import TextEmbedding; from dungeonmind.hub.app import NOTE_MODEL; TextEmbedding(NOTE_MODEL)"

COPY --from=web /web/dist ./web/dist

# K2: non-root; hub data on a volume. The embedded index takes a lock file, so it's writable.
RUN useradd --uid 10001 --create-home dungeonmind \
 && mkdir -p /data/hub \
 && chown -R dungeonmind /data/hub /app/data/index
USER dungeonmind
VOLUME /data/hub
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

CMD ["dungeonmind", "serve", "--host", "0.0.0.0", "--port", "8000", "--root", "/data/hub"]
