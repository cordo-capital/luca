# The image: `luca serve` without installing Python or uv. Packaging, not deployment —
# no path, no société, no tunnel lives here; those belong to whoever runs it.

# Build: the virtualenv, from uv.lock exactly — the versions CI tested, not the newest.
FROM ghcr.io/astral-sh/uv:0.8.17-python3.12-bookworm-slim AS build
WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1
COPY pyproject.toml uv.lock README.md ./
COPY src src
RUN uv sync --locked --no-dev --no-editable

# Run: python slim, the virtualenv at the same path, a non-root user, /data.
FROM python:3.12-slim-bookworm

# One log line per request on stdout, delivered as it happens.
ENV PYTHONUNBUFFERED=1 PATH=/app/.venv/bin:$PATH

RUN useradd --create-home --uid 1000 luca && mkdir /data && chown luca /data
COPY --from=build /app/.venv /app/.venv

USER luca
VOLUME /data
EXPOSE 8000
# Inside the container the network namespace is the boundary, and the port mapping
# decides what is reachable (ADR 0003): bind to every interface of the container.
ENTRYPOINT ["luca", "serve", "--host", "0.0.0.0"]
CMD ["--db", "/data/luca.db"]
