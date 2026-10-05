FROM ghcr.io/astral-sh/uv:0.12.21@sha256:a7aed3216253ee804de3e2d8afa5073baa1a177335345d43845cd4165e43b711 AS uv

FROM python:3.12-slim@sha256:02108f5d322dd89f1c9e552442c25acb0543dfdbc455693a5599624f20d9155d AS build
COPY --from=uv /uv /usr/local/bin/uv
# The base image's Python builds the wheel (it is py3-none-any); a downloaded interpreter
# would sit outside the pinned digests.
ENV UV_PYTHON_DOWNLOADS=never
WORKDIR /src
COPY . .
# pyproject.toml pins the build backend, so the wheel matches the one the release builds.
RUN uv build --wheel --python /usr/local/bin/python3 --out-dir /dist \
    && uv export --locked --no-dev --extra serve --extra zstd --no-emit-project \
        --python /usr/local/bin/python3 -o /dist/requirements.txt

FROM python:3.12-slim@sha256:02108f5d322dd89f1c9e552442c25acb0543dfdbc455693a5599624f20d9155d
# Log lines reach `docker logs` as they are written, not when a buffer fills.
ENV PYTHONUNBUFFERED=1
COPY --from=build /dist /tmp/dist
# Dependencies come from uv.lock, hash-checked; the wheel then installs without resolving anything.
RUN pip install --no-cache-dir --require-hashes -r /tmp/dist/requirements.txt \
    && pip install --no-cache-dir --no-deps /tmp/dist/*.whl \
    && pip check \
    && rm -rf /tmp/dist \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin detecttrace \
    && mkdir /data \
    && chown detecttrace /data
USER detecttrace
WORKDIR /data
VOLUME /data
EXPOSE 4320
# The slim image has no curl. It checks plain HTTP on the default port: a config that moves the
# port or turns on TLS needs its own healthcheck.
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4320/healthz', timeout=3)"]
# Python runs as PID 1; uvicorn handles SIGTERM itself, so `docker stop` is a graceful stop.
ENTRYPOINT ["detecttrace"]
CMD ["serve", "--config", "/data/detecttrace-serve.yaml"]
