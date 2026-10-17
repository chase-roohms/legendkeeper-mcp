# syntax=docker/dockerfile:1.7
ARG PYTHON_VERSION=3.13

FROM python:${PYTHON_VERSION}-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
# Dependencies first, for layer caching.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY README.md LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM python:${PYTHON_VERSION}-slim AS runtime
ARG VERSION=dev
LABEL org.opencontainers.image.title="legendkeeper-mcp" \
      org.opencontainers.image.description="Unofficial MCP server for the LegendKeeper worldbuilding API" \
      org.opencontainers.image.source="https://github.com/chase-roohms/legendkeeper-mcp" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      io.modelcontextprotocol.server.name="io.github.chase-roohms/legendkeeper-mcp"
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin legendkeeper \
    && mkdir -p /uploads && chown legendkeeper /uploads
COPY --from=build /app/.venv /app/.venv
COPY docker/healthcheck.py /app/healthcheck.py
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000 \
    LEGENDKEEPER_UPLOAD_ROOT=/uploads
USER legendkeeper
WORKDIR /home/legendkeeper
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD ["python", "/app/healthcheck.py"]
ENTRYPOINT ["legendkeeper-mcp"]
