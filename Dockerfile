# The image: `luca serve` without installing Python or uv. Packaging, not deployment —
# no path, no société, no tunnel lives here; those belong to whoever runs it.
FROM python:3.12-slim

# One log line per request on stdout, delivered as it happens.
ENV PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 1000 luca && mkdir /data && chown luca /data

COPY pyproject.toml README.md /build/
COPY src /build/src
RUN pip install --no-cache-dir /build && rm -rf /build

USER luca
VOLUME /data
EXPOSE 8000
# Inside the container the network namespace is the boundary, and the port mapping
# decides what is reachable (ADR 0003): bind to every interface of the container.
ENTRYPOINT ["luca", "serve", "--host", "0.0.0.0"]
CMD ["--db", "/data/luca.db"]
