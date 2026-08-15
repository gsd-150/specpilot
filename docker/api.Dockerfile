# syntax=docker/dockerfile:1
# Build the common Python payload without browser assets. Initializers use this
# lineage directly, so a cold initializer build never downloads Node packages
# and cannot accidentally expose the trace UI.
FROM python:3.12-slim AS python-build

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN rm -rf src/specpilot/api/static/trace \
    && python -m pip install --no-cache-dir --upgrade pip build \
    && python -m build --wheel --outdir /wheels

FROM python:3.12-slim AS python-runtime

RUN useradd --create-home --uid 10001 specpilot
COPY --from=python-build /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/*.whl uvicorn \
    && rm -rf /wheels

USER specpilot
WORKDIR /home/specpilot

# Real initialization shares the exact installed Python/W5 payload with the API
# but stops before the frontend lineage.
FROM python-runtime AS initializer-runtime
ENTRYPOINT ["python", "-m", "specpilot.cli"]

# Synthetic bytes exist only in this demo initializer target.
FROM initializer-runtime AS fixture
USER root
COPY --chown=10001:10001 fixtures/demo /run/specpilot/fixture
USER specpilot

# Only the packaged API builds and receives the browser trace application.
FROM node:22.12-bookworm-slim AS frontend

WORKDIR /build
COPY web/trace/package.json web/trace/package-lock.json ./web/trace/
RUN npm --prefix web/trace ci
COPY web/trace ./web/trace
RUN npm --prefix web/trace run build

FROM python-runtime AS runtime
USER root
COPY --from=frontend /build/src/specpilot/api/static/trace \
    /usr/local/lib/python3.12/site-packages/specpilot/api/static/trace
USER specpilot

EXPOSE 8000
# Bound to all interfaces inside the container only; Compose decides whether the
# port is published, and only the demo profile does.
CMD ["python", "-m", "uvicorn", "--factory", "specpilot.api.runtime:create_runtime_app", \
     "--host", "0.0.0.0", "--port", "8000"]
