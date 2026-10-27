# One image for the checkout service and the fake pager: the compose file picks the command.
FROM python:3.12.8-slim-bookworm@sha256:2199a62885a12290dc9c5be3ca0681d367576ab7bf037da120e564723292a2f0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.lock ./
# The cache mount keeps downloaded wheels between builds; it is not part of the image.
RUN --mount=type=cache,target=/root/.cache/pip pip install --require-hashes -r requirements.lock

COPY pyproject.toml README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/pip pip install --no-deps .

# /data is where the fake pager keeps its pages; a named volume mounted there inherits this owner.
RUN useradd --system --uid 10001 --no-create-home app && mkdir /data && chown app /data
USER app

# opentelemetry-instrument applies the automatic Flask instrumentation and reads the OTEL_* variables.
CMD ["opentelemetry-instrument", "slo-service"]
