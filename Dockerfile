# One image: the API serves the built web app. Data lives in /app/data (mount a volume).

FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build


FROM python:3.12-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv
RUN pip install --no-cache-dir "uv==0.11.32"

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY backend/ ./
RUN uv sync --locked --no-dev

WORKDIR /app
COPY --from=web /web/dist /app/web
RUN useradd --create-home --uid 1000 hisaab && mkdir -p /app/data && chown hisaab:hisaab /app/data

ENV PATH=/opt/venv/bin:$PATH \
    HISAAB_ENV_FILE=/app/data/.env \
    HISAAB_STATIC_DIR=/app/web \
    HISAAB_HOST=0.0.0.0 \
    HISAAB_PORT=8000

USER hisaab
VOLUME ["/app/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"
CMD ["hisaab", "serve"]
