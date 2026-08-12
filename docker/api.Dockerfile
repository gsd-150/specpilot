# syntax=docker/dockerfile:1
FROM python:3.12-slim AS build

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir --upgrade pip build \
    && python -m build --wheel --outdir /wheels

FROM python:3.12-slim AS runtime

# Unprivileged by default. Nothing in the API needs to write to its own image.
RUN useradd --create-home --uid 10001 specpilot
COPY --from=build /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/*.whl uvicorn \
    && rm -rf /wheels

USER specpilot
WORKDIR /home/specpilot
EXPOSE 8000

# Bound to all interfaces inside the container only; Compose decides whether the
# port is published, and only the demo profile does.
CMD ["python", "-m", "uvicorn", "--factory", "specpilot.api.runtime:create_runtime_app", \
     "--host", "0.0.0.0", "--port", "8000"]
