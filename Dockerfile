FROM ghcr.io/astral-sh/uv:0.12.21@sha256:a7aed3216253ee804de3e2d8afa5073baa1a177335345d43845cd4165e43b711 AS uv

FROM python:3.12-slim@sha256:02108f5d322dd89f1c9e552442c25acb0543dfdbc455693a5599624f20d9155d AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /src
COPY . .
# pyproject.toml pins the build backend, so the wheel matches the one the release builds.
RUN uv build --wheel --out-dir /dist

FROM python:3.12-slim@sha256:02108f5d322dd89f1c9e552442c25acb0543dfdbc455693a5599624f20d9155d
COPY --from=build /dist /tmp/dist
# A shell glob can't carry an extra, so resolve the wheel's file name first.
RUN set -eu; \
    wheel="$(ls /tmp/dist/*.whl)"; \
    pip install --no-cache-dir "${wheel}[zstd]"; \
    rm -rf /tmp/dist; \
    useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin detecttrace; \
    mkdir /data; \
    chown detecttrace /data
USER detecttrace
WORKDIR /data
VOLUME /data
ENTRYPOINT ["detecttrace"]
CMD ["--help"]
