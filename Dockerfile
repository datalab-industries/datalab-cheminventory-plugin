FROM debian:stable-slim AS base

LABEL org.opencontainers.image.source=https://github.com/datalab-industries/datalab-cheminventory-plugin
LABEL org.opencontainers.image.description="datalab-cheminventory plugin: for two-way sync between datalab and cheminventory.net"
LABEL org.opencontainers.image.licenses=MIT

COPY --from=ghcr.io/astral-sh/uv:0.6.4 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/.venv \
    UV_PYTHON=python3.10

WORKDIR /opt
COPY ./pyproject.toml .
COPY ./uv.lock .
RUN uv python install 3.10 && uv sync --locked --no-dev

COPY ./src/ /opt/src/

CMD ["uv", "run", "datalab-cheminventory-sync"]
