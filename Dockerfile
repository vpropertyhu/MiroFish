FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN npm ci --prefix frontend
COPY frontend ./frontend
COPY locales ./locales
RUN npm run build --prefix frontend

FROM python:3.12-slim-bookworm AS source
WORKDIR /source
COPY . .
RUN python cloud/package_source.py /source /source.zip

FROM python:3.12-slim-bookworm AS runtime
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /bin/uv
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    FLASK_DEBUG=False MIROFISH_DATA_DIR=/var/data/mirofish \
    MIROFISH_MAX_AGENTS=8 MIROFISH_MAX_ROUNDS=20 MIROFISH_NEW_PROJECTS_PER_DAY=3 \
    MIROFISH_AI_REQUESTS_PER_DAY=24 REPORT_AGENT_MAX_TOOL_CALLS=3 \
    OASIS_DEFAULT_MAX_ROUNDS=20
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./backend/
RUN cd backend && uv sync --frozen --no-dev --no-install-project
COPY backend ./backend
RUN cd backend && uv sync --frozen --no-dev
COPY locales ./locales
COPY --from=frontend /build/frontend/dist ./frontend/dist
COPY cloud ./cloud
COPY --from=source /source.zip ./cloud/source.zip
COPY LICENSE ./LICENSE
RUN mkdir -p /var/data/mirofish
EXPOSE 10000
CMD ["/bin/sh", "-c", "exec /app/backend/.venv/bin/gunicorn --chdir /app/backend --bind 0.0.0.0:${PORT:-10000} --workers 1 --threads 8 --timeout 180 --graceful-timeout 650 shared_wsgi:app"]
