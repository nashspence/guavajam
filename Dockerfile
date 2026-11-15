FROM ghcr.io/astral-sh/uv:0.11.24@sha256:99ea34acedc870ba4ad11a1f540a1c04267c9f30aadc465a94406f52dfda2c36 AS uv
FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS packages
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /build
COPY pyproject.toml uv.lock ./
COPY projects/core/pyproject.toml projects/core/pyproject.toml
COPY projects/view/pyproject.toml projects/view/pyproject.toml
COPY projects/core/src projects/core/src
COPY projects/view/src projects/view/src
RUN uv sync --locked --all-packages --no-dev --no-editable

FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS view
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git openssh-client gnupg libgmp10 && rm -rf /var/lib/apt/lists/*
COPY deploy/install_pandoc.py /tmp/install_pandoc.py
RUN python /tmp/install_pandoc.py && rm /tmp/install_pandoc.py
COPY --from=packages /build/.venv /opt/guavajam
ENV PATH="/opt/guavajam/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 GIT_TERMINAL_PROMPT=0
RUN mkdir /state && chown 1000:1000 /state
USER 1000:1000
WORKDIR /state
ENTRYPOINT ["python", "-m", "guavajam.service"]

FROM node:24.10.0-bookworm-slim@sha256:b8d2197aff9129d16c801a3e3e1b2a873c4946480f5a310f38056df2268c38d9 AS gateway
COPY --from=packages /build/.venv/lib/python3.12/site-packages/guavajam_core/resources/storage /opt/gateway
USER 1000:1000
WORKDIR /opt/gateway
ENTRYPOINT ["node", "server.mjs"]

FROM nginx:stable-alpine@sha256:901e593c95dc85f5a729f5765516321366ef4ac401821431d408897e95287d0a AS web
COPY --chmod=644 deploy/nginx.conf /etc/nginx/nginx.conf
USER 101:101
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
