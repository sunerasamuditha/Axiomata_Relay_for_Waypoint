# Relay: one image, one process. FastAPI serves /api, the live stream (SSE) and the built React app
# on the same origin. Used by `docker compose up` and by Cloud Run (`gcloud run deploy --source .`).
# Plain Dockerfile syntax (no BuildKit-only features) so every builder, Cloud Build included, accepts it.

# ---- 1. web: build the React app ----------------------------------------------------------------
FROM node:24-bookworm-slim AS web
ENV CI=true
RUN npm install -g pnpm@10.18.0
WORKDIR /src
# manifests first: the dependency layer is reused until the lockfile changes
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/
COPY e2e/package.json e2e/
RUN pnpm install --frozen-lockfile --filter @relay/web
COPY apps/web apps/web
RUN pnpm --filter @relay/web build

# ---- 2. app: Python runtime ---------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH=/app/.venv/bin:$PATH
# libgomp1: the OpenMP runtime LightGBM needs
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir uv==0.8.17
WORKDIR /app

# third-party dependencies (runtime only: no dev tools, no training extras)
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/
COPY packages/engine/pyproject.toml packages/engine/
COPY packages/ml/pyproject.toml packages/ml/
RUN uv sync --frozen --no-dev --no-install-workspace --package relay-api

# our code, the seed data and the trained models (models are absent on a fresh clone: the
# predictor then uses its documented heuristic)
COPY packages packages
COPY apps/api apps/api
COPY data/reference data/reference
COPY data/demo data/demo
COPY scripts/entrypoint.sh scripts/entrypoint.sh
RUN uv sync --frozen --no-dev --package relay-api \
 && chmod +x scripts/entrypoint.sh \
 && useradd --system --uid 10001 --home-dir /app relay
COPY --from=web /src/apps/web/dist apps/web/dist

ENV PORT=8080 \
    SEED_ON_START=false \
    RELAY_MODEL_DIR=/app/packages/ml/models
USER relay
EXPOSE 8080
# migrations → seed what is missing (advisory-locked) → uvicorn
CMD ["/app/scripts/entrypoint.sh"]
