# syntax=docker/dockerfile:1
# Home Assistant MCP Server Add-on (Experimental)
# Clones source from GitHub and builds locally.
# Python 3.13 - Security support until 2029-10

# --- Build stage: clone repo and install with uv ---
FROM ghcr.io/astral-sh/uv:0.11.0-python3.13-trixie-slim@sha256:5b216b72b3bc10f983f82b39b5386455bfa08d2139afb4cb3f6c9f060484ea5d AS builder

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Clone the repo (uses master branch)
# CACHE_BUST: change this value to force a fresh git clone on rebuild
ARG CACHE_BUST=3
ARG REPO_URL=https://github.com/TomasDJo/ha-mcp.git
ARG REPO_BRANCH=master
RUN git clone --depth 1 --branch ${REPO_BRANCH} ${REPO_URL} /repo

# Copy project files from cloned repo
RUN cp /repo/pyproject.toml /repo/uv.lock /app/
RUN cp -r /repo/src /app/src

# Compile bytecode for faster startup; copy mode required with cache mounts
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Install dependencies and project
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# --- Runtime stage: clean image without uv ---
FROM python:3.13-slim@sha256:3de9a8d7aedbb7984dc18f2dff178a7850f16c1ae7c34ba9d7ecc23d0755e35f

WORKDIR /app

# Copy the virtual environment and startup script
COPY --from=builder /app/.venv /app/.venv
COPY start.py /

# Activate virtual environment via PATH
ENV PATH="/app/.venv/bin:$PATH"

# Labels
ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL \
    io.hass.name="Home Assistant MCP Server (Experimental)" \
    io.hass.description="Experimental fork — AI assistant integration via Model Context Protocol" \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="addon" \
    io.hass.arch="${BUILD_ARCH}"

CMD ["python3", "/start.py"]
