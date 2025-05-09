FROM debian:stable-slim AS base

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
