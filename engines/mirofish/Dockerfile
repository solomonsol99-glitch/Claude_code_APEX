# syntax=docker/dockerfile:1.7
# MiroFish engine image (AGPL-3.0 zone). Backend only: no Node, no frontend.
# Built ONLY from the pinned upstream commit plus patches/*.patch (applied in order).
# Coreset code never imports anything from this image; it is reached over HTTP only.

# Build stage uses the full image (ships git, patch and compilers, so no apt step);
# runtime uses slim. Keep both on the same Python minor version.
ARG REGISTRY_PREFIX=
ARG PYTHON_VERSION=3.12
ARG MIROFISH_REPO=https://github.com/666ghj/MiroFish
ARG MIROFISH_REF=7657031ac01184afe2cb220f5ee3545573b5e843
ARG UV_VERSION=0.9.26

FROM ${REGISTRY_PREFIX}python:${PYTHON_VERSION}-bookworm AS build
ARG MIROFISH_REPO
ARG MIROFISH_REF
ARG UV_VERSION
# Optional extra CA (e.g. a corporate or sandbox TLS proxy), passed as a BuildKit secret:
#   docker build --secret id=extra_ca,src=/path/ca.crt ...
# It is used only in this build stage and never reaches the runtime image.
ENV CA_SECRET=/run/secrets/extra_ca
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s "$CA_SECRET" ]; then export PIP_CERT="$CA_SECRET"; fi; \
    pip install --no-cache-dir "uv==${UV_VERSION}"
WORKDIR /src
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s "$CA_SECRET" ]; then export GIT_SSL_CAINFO="$CA_SECRET"; fi; \
    git init -q mirofish \
 && git -C mirofish remote add origin "${MIROFISH_REPO}" \
 && git -C mirofish fetch -q --depth 1 origin "${MIROFISH_REF}" \
 && git -C mirofish checkout -q FETCH_HEAD \
 && test "$(git -C mirofish rev-parse HEAD)" = "${MIROFISH_REF}"
COPY patches/ /src/patches/
RUN set -e; cd /src/mirofish; \
    for p in $(ls /src/patches/*.patch 2>/dev/null | sort); do \
      echo "applying $p"; patch -p1 --forward < "$p"; \
    done
WORKDIR /src/mirofish/backend
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s "$CA_SECRET" ]; then export SSL_CERT_FILE="$CA_SECRET"; fi; \
    uv sync --frozen --no-dev --no-install-project

FROM ${REGISTRY_PREFIX}python:${PYTHON_VERSION}-slim-bookworm AS runtime
ARG MIROFISH_REF
LABEL org.opencontainers.image.title="coreset-mirofish-engine" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.source="https://github.com/666ghj/MiroFish" \
      org.opencontainers.image.revision="${MIROFISH_REF}" \
      mre.engine="true"
RUN groupadd --gid 10001 engine && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin engine
WORKDIR /opt/mirofish
COPY --from=build --chown=0:0 /src/mirofish/backend /opt/mirofish/backend
COPY --from=build --chown=0:0 /src/mirofish/LICENSE /opt/mirofish/LICENSE
COPY --chown=0:0 NOTICE SOURCE_OFFER.md /opt/mirofish/
# uploads/ is a size-limited tmpfs at runtime (read-only rootfs); keep the mount point owned by the engine user.
RUN mkdir -p /opt/mirofish/backend/uploads && chown 10001:10001 /opt/mirofish/backend/uploads
ENV PATH="/opt/mirofish/backend/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_DEBUG=false \
    FLASK_HOST=0.0.0.0 \
    FLASK_PORT=5001 \
    MIROFISH_REF=${MIROFISH_REF}
USER 10001:10001
WORKDIR /opt/mirofish/backend
EXPOSE 5001
HEALTHCHECK --interval=15s --timeout=2s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5001/health', timeout=2).status==200 else 1)"
CMD ["python", "run.py"]
