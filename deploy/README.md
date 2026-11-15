# Compose deployment

This builds the unreleased view and core from the checked-out source. No image is published and no edition is signed by these commands. Docker Compose v2+ is required. Image inputs are pinned by digest; Python packages come from `uv.lock`, and Pandoc's official package is SHA-256 verified. The host needs Docker only; mise/uv remain the normal source-development tools.

## View

1. Copy `deploy/compose.env.example` to an owner-controlled file outside this public checkout. Set `GUAVAJAM_SOURCE_URL` to an HTTPS or SSH Git URL, and set `GUAVAJAM_TRUST_DIR` to an existing absolute directory. The source must have the signed site checkpoint and publications described in [updates](../projects/view/docs/updates.md).
2. Provision independent signer trust in that directory: `allowed_signers` for SSH signatures, or `publisher.asc` plus the full `GUAVAJAM_TRUSTED_GPG_FINGERPRINTS` for GPG. For GPG clear `GUAVAJAM_SSH_ALLOWED_SIGNERS` and set `GUAVAJAM_GPG_PUBLIC_KEYS=/trust/publisher.asc`. These are verification public keys; publication signing keys are not needed.
3. From this repository, run:

```sh
docker compose --env-file /path/to/compose.env up --build -d
```

`web` listens on `127.0.0.1:8080` by default. Put your HTTPS reverse proxy in front of it. Another containerized proxy can join the Compose network through an operator-owned override and route to `web:8080`; no Docker socket is mounted. The application does not configure DNS, ingress, registrar credentials or authentication for your host. Put access control at ingress for a private publication.

For private Git transport, put a dedicated read-only transport key, pinned `known_hosts`, and `ssh_config` in the trust directory. The container uses `ssh -F /trust/ssh_config -o BatchMode=yes -o StrictHostKeyChecking=yes`. Name the files in that configuration using `/trust/...` paths. Mounted files must be readable by the updater's UID/GID `1000:1000`; retain owner-only permissions for the transport key. Never put tokens in the repository URL.

The updater creates a full, no-checkout clone in the `state` volume and polls `refs/heads/main` every 300 seconds. Configure `GUAVAJAM_SOURCE_REF` and `GUAVAJAM_POLL_SECONDS` as needed. An operator-mounted trusted local Git repository can instead use a `file:///...` URL through a Compose override. If that mounted local repository has a different numeric owner, an operator-owned Git configuration may list only that reviewed repository path and its `.git` directory in `safe.directory`; mount the config and select it with `GIT_CONFIG_GLOBAL` in your override. Do not use a wildcard trust entry. HTTPS/SSH clones do not need this local-mount exception. A different publisher URL requires separate state; startup never silently resets the retained baseline.

The web server serves only `site/current`. Source clones, private caches, receipts and earlier generations are outside its document root. Signed-source verification, incremental reuse, history checks and atomic switching use the existing updater. A failed update leaves the previous complete site selected and marks the updater unhealthy. `docker compose ps` exposes both updater freshness and web readiness. The updater logs fixed status categories and the failing step (`source`, `trust`, or `update`), without transport URLs or credentials.

## Optional local assets

Enable profile `local-assets` to add **SeaweedFS mini** and the read-only asset gateway. The gateway's HTTP adapter imports the same `worker.js` used by Cloudflare; identity checks, headers, ETags, streaming limits and range handling have one implementation. Any existing compatible S3 endpoint can also be used by starting `assets` alone and omitting SeaweedFS.

Build the images, create an owner-controlled private parent directory, and generate separate bucket-scoped publisher and delivery identities:

```sh
docker compose --env-file /path/to/compose.env build updater web assets
mkdir -m 700 /path/to/private
docker run --rm --entrypoint python   --user "$(id -u):$(id -g)" -v /path/to/private:/private   guavajam-view:local -m guavajam_core.local_assets /private/assets
```

The initializer refuses to overwrite existing files and never prints credentials. Set these paths in the private Compose environment file:

```dotenv
SEAWEEDFS_IDENTITIES=/path/to/private/assets/s3-identities.json
S3_ACCESS_KEY_ID_FILE=/path/to/private/assets/delivery-access-key
S3_SECRET_ACCESS_KEY_FILE=/path/to/private/assets/delivery-secret-key
ASSET_HOST=assets.publisher.example
S3_ENDPOINT=https://s3.publisher.example
S3_REGION=us-east-1
S3_BUCKET=publication-assets
```

The generated directory is owner-only and credential files are mode `0600`. The asset container uses UID/GID `1000:1000`; it must be able to read its two delivery files. Configure the publisher separately with its own generated credential files and the same HTTPS S3 endpoint using [core storage configuration](../docs/storage.md). The web server and view updater receive no S3 credentials.

```sh
docker compose --env-file /path/to/compose.env --profile local-assets up --build -d
```

SeaweedFS persists its data and metadata in `objects`. It creates `S3_BUCKET` on first startup and loads the private identity file; the application supplies no anonymous identity. The profile publishes only its S3 port on loopback (`8333`), with the asset adapter on loopback (`8081`). Master/filer/volume/admin ports are not published. Default local operation uses `S3_VERSIONING=off`, `S3_OBJECT_LOCK=off`, `S3_CONDITIONAL_WRITES=off`. Enable optional capabilities only after configuring and qualifying them on the chosen bucket. Retention is an explicit publisher decision; the private publication can use optional-retention preparation, while a different public publisher may require retention.

Provide HTTPS ingress for **both** canonical origins. For a proxy attached to the Compose network, route `s3.publisher.example` to `seaweedfs:8333` and `assets.publisher.example` to `assets:8080`. Keep the S3 Host and request path intact and disable proxy caching, request buffering and body-size limits for multipart uploads. For example, within the appropriate TLS virtual host:

```nginx
location / {
    proxy_pass http://seaweedfs:8333;
    proxy_set_header Host $http_host;
    proxy_http_version 1.1;
    proxy_request_buffering off;
    proxy_buffering off;
    client_max_body_size 0;
}
```

Use the same header/buffering settings with `proxy_pass http://assets:8080` for the asset host. Standard Range/If-Match headers must pass through unchanged. Certificates must validate for the configured hostname. For an operator CA, mount its public certificate and configure `SSL_CERT_FILE` for Python operators and `NODE_EXTRA_CA_CERTS` for the gateway through a private override; TLS verification remains enabled.

If the canonical asset hostname resolves to LAN or Docker addresses, put an operator-owned policy outside the publisher repository, based on [asset-network.example.json](asset-network.example.json), and set `GUAVAJAM_ASSET_NETWORK_POLICY` for publisher verification commands. The policy authorizes only that exact HTTPS origin and listed private CIDRs; all DNS answers must be public or explicitly authorized. It does not authorize other hosts, HTTP, redirects, arbitrary ports or link-local metadata endpoints. TLS hostname verification and DNS pinning remain mandatory. The view links directly to this canonical origin, so browsers must also be able to resolve/reach it.

## State and updates

`docker compose down` retains both named volumes. Rebuild/recreate to upgrade the pinned application. Back up `state` (including retained site generations and catalog baselines), `objects`, and the operator's trust/configuration separately using your host's normal consistent-backup procedure. A single-node object store has one storage failure domain; its local versions/locks do not replace independent custody.

Do not run `down --volumes` on a publication deployment unless intentionally deleting its state. Do not recreate an empty catalog baseline as recovery. Restore the retained complete generation and baseline before restarting updates. See [the updater recovery contract](../projects/view/docs/updates.md).
