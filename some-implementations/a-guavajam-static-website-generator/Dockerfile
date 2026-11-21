FROM ghcr.io/astral-sh/uv:0.11.24@sha256:99ea34acedc870ba4ad11a1f540a1c04267c9f30aadc465a94406f52dfda2c36 AS uv
FROM node:24.10.0-bookworm-slim@sha256:b8d2197aff9129d16c801a3e3e1b2a873c4946480f5a310f38056df2268c38d9 AS search_assets
WORKDIR /search
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund

FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS packages
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /build
COPY pyproject.toml uv.lock ./
COPY some-implementations/a-guavajam-static-website-generator/pyproject.toml some-implementations/a-guavajam-static-website-generator/pyproject.toml
COPY LICENSES LICENSES
COPY REUSE.toml MANIFEST.in ./
COPY some-implementations/a-guavajam-static-website-generator/LICENSES some-implementations/a-guavajam-static-website-generator/LICENSES
COPY some-implementations/a-guavajam-static-website-generator/REUSE.toml some-implementations/a-guavajam-static-website-generator/REUSE.toml
COPY src src
COPY some-implementations/a-guavajam-static-website-generator/src some-implementations/a-guavajam-static-website-generator/src
COPY --from=search_assets /search/node_modules/@sqlite.org/sqlite-wasm/dist/index.mjs some-implementations/a-guavajam-static-website-generator/src/a_guavajam_static_website_generator/assets/sqlite3.mjs
COPY --from=search_assets /search/node_modules/@sqlite.org/sqlite-wasm/dist/sqlite3.wasm some-implementations/a-guavajam-static-website-generator/src/a_guavajam_static_website_generator/assets/sqlite3.wasm
RUN uv sync --locked --all-packages --no-dev --no-editable

FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS a-guavajam-static-website-generator
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git openssh-client gnupg libgmp10 && rm -rf /var/lib/apt/lists/*
COPY some-implementations/a-guavajam-static-website-generator/deploy/install_pandoc.py /tmp/install_pandoc.py
RUN python /tmp/install_pandoc.py && rm /tmp/install_pandoc.py
COPY --from=packages /build/.venv /opt/guavajam
ENV PATH="/opt/guavajam/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 GIT_TERMINAL_PROMPT=0 HOME=/state
RUN groupadd --gid 1000 guavajam && useradd --uid 1000 --gid guavajam --home-dir /state --no-create-home --shell /usr/sbin/nologin guavajam && mkdir /state && chown 1000:1000 /state
USER 1000:1000
WORKDIR /state
ENTRYPOINT ["python", "-m", "a_guavajam_static_website_generator.service"]

FROM node:24.10.0-bookworm-slim@sha256:b8d2197aff9129d16c801a3e3e1b2a873c4946480f5a310f38056df2268c38d9 AS node

FROM a-guavajam-static-website-generator AS preview
COPY --from=node /usr/local/bin/node /usr/local/bin/node
ENTRYPOINT ["python", "-m", "a_guavajam_static_website_generator.preview_service"]

FROM node AS gateway
COPY --from=packages /build/.venv/lib/python3.12/site-packages/guavajam/resources/storage /opt/gateway
COPY --from=packages /build/LICENSES /usr/share/licenses/guavajam/LICENSES
COPY --from=packages /build/REUSE.toml /usr/share/licenses/guavajam/REUSE.toml
USER 1000:1000
WORKDIR /opt/gateway
ENTRYPOINT ["node", "server.mjs"]

FROM nginx:stable-alpine@sha256:901e593c95dc85f5a729f5765516321366ef4ac401821431d408897e95287d0a AS web
COPY --chmod=644 some-implementations/a-guavajam-static-website-generator/deploy/nginx.conf /etc/nginx/nginx.conf
COPY LICENSES /usr/share/licenses/guavajam/LICENSES
COPY REUSE.toml /usr/share/licenses/guavajam/REUSE.toml
USER 101:101
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
