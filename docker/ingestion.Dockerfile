# syntax=docker/dockerfile:1
#
# The ingestion image is the one that touches untrusted documents, so it is the
# one that is locked down. It has no network, no writable root filesystem, no
# capabilities, and no entrypoint other than the narrow inspection worker.
FROM python:3.12-slim AS build

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir --upgrade pip build \
    && python -m build --wheel --outdir /wheels

FROM python:3.12-slim AS runtime

RUN useradd --create-home --uid 10002 ingestion \
    && mkdir -p /input /output \
    && chown ingestion:ingestion /output
COPY --from=build /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

USER ingestion
WORKDIR /home/ingestion

# Reads one already-extracted DOCX from a read-only mount and writes one
# inspection record. It never parses document text, never resolves a
# relationship target, and never opens a socket.
ENTRYPOINT ["python", "-m", "specpilot.ingestion.sandbox_worker"]
CMD ["inspect", "--input", "/input/source.docx", "--output", "/output/inspection.json"]
