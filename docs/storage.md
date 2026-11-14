# Asset storage

Export `make asset-credentials`, fill the owner-only `.env.assets` file, and configure an existing private S3-compatible bucket. `S3_ENV` selects another private configuration file. The public asset origin in `asset-policy.json` is independent of the storage endpoint. `make gateway-plan` is read-only; `make gateway-deploy` installs the Cloudflare delivery Worker. Core publication verification and archives do not depend on Cloudflare's deployment API.

Set `S3_ENDPOINT` to the HTTPS service origin (no bucket, path, query, credentials or fragment), `S3_REGION` to its signing region, and `S3_BUCKET` to the bucket name. Custom HTTPS ports and private/self-hosted endpoints are supported. `S3_ADDRESSING_STYLE` is `path` (default) or `virtual`. Credentials are `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, and optionally `S3_SESSION_TOKEN`. Temporary credentials must be replaced before they expire; the Worker does not refresh them.

Typical endpoint/region pairs are `https://s3.us-west-2.amazonaws.com` / `us-west-2`, `https://s3.us-west-004.backblazeb2.com` / `us-west-004`, and `https://ACCOUNT_ID.r2.cloudflarestorage.com` / `auto`. Use the actual region assigned to the bucket. Endpoint and region are explicit; there is no Backblaze login, hostname-based region inference, bucket provisioning, credential discovery, or redirect following.

## Optional capabilities

The defaults, `S3_VERSIONING=off` and `S3_OBJECT_LOCK=off`, support a plain unversioned bucket. Uploading, pinning, public delivery, publication preparation, verification, and collection work without versioning or retention APIs.

Set `S3_VERSIONING=on` for a versioned bucket, including B2. This enables complete version and delete-marker inventory for reuse and collection. Reads pin a returned version ID when present; IDs are opaque, not provider-specific. Collection refuses a versioned object discovered through an unversioned inventory rather than overlooking its history. S3's mutable `null` version is ETag-guarded, not treated as an immutable version.

Set `S3_OBJECT_LOCK=on` only for an Object Lock-enabled, versioned bucket. This enables explicit retention and legal-hold inspection. Existing observed retention is honored. To extend retention, use `make asset-pin KEY=... RETAIN_UNTIL=EPOCH_MS RETENTION_MODE=governance` (or `compliance`). The asset and its pin sidecar are verified; retention is never shortened, modes are not silently changed, and governance bypass is never requested. Explicit retention on an unsupported or unauthorized store fails. Permission errors are never interpreted as an absent capability.

`make publication-prepare ...` and incoming online publication checks do **not** require retention by default. Preparation reports `retention_verified: false` when dependencies have no verified current commitment. `REQUIRE_RETENTION=1` on the preparation recipe, or `--require-retention` on the preparation/incoming-check CLI, makes current retention mandatory. Full SHA-256 validation remains mandatory in either mode.

`S3_CONDITIONAL_WRITES=on` optionally enables `If-None-Match` creation and `If-Match` sidecar updates on services implementing conditional PUT and multipart completion. An error never triggers an unconditional retry. The default `off` uses preflight identity checks and verified readback for compatibility; these checks are not atomic. Serialize mutating operators across machines/checkouts. A checkout-local lock is not a distributed lock.

## Identity and guarantees

Asset URLs still contain the original bytes' SHA-256. The private `press.asset.v1` pin records that digest, MIME type, byte count, a strong ETag, a nullable version ID, and nullable retention mode/time. No credential or provider-specific bucket identifier enters the pin or signed publication.

All asset GET, HEAD, and range reads send `If-Match` and verify the returned ETag, byte counts, type, encoding and, when present, version ID. A changed or missing origin object fails closed; it is not silently repinned. ETags are opaque HTTP validators, **not** SHA-256 hashes or durable object versions. Full bytes are hashed during ingestion and publication verification. Codec range probes use the same ETag as the fully hashed response.

The gateway always exposes `ETag`. `X-Press-Asset-Version` is present only for an immutable version and is percent-encoded; it is absent for unversioned and `null`-version objects. `X-Press-Retain-Until: 0` means no retention commitment is claimed. The gateway never invents a version or lock guarantee for a plain bucket.

Unversioned storage provides verified, content-addressed delivery, not protection against an operator deleting or replacing bytes. Versioning permits reading an older version but does not prohibit its deletion. Object Lock protects particular versions during their retention period; it does not stop new versions or pointer changes. Keep independent source/asset archives for preservation beyond the chosen provider's guarantees.

## Permissions and maintenance

Scope credentials to the configured bucket and `sha256/` namespace. Baseline operators need object read/write and `ListObjectsV2`; collection also needs object deletion. Versioned operation adds version listing/read/delete permissions. Object Lock adds retention and legal-hold reads, and retention writes for explicit extensions. Multipart media uses initiate, upload-part, complete and abort operations. No bucket-management API is called. S3 has no portable equivalent of B2 key-scope introspection: enforce least privilege at the provider. Deploy the Worker with a separate read-only key; it needs object reads, including version reads when used, but no listing, writes or retention permissions.

Cleanup retains referenced digests, their leases/pins and every known version, observes the 30-day grace period and verified locks/legal holds, and refreshes both storage and Git roots before deleting. Delete markers are removed after older content. **Pause all uploads, lease renewals, pin updates and branch/PR changes for the entire applied sweep.** Plain S3 does not provide a universal atomic compare-and-delete; unversioned deletes recheck metadata but still require quiescence. Do not run an independent lifecycle rule that removes referenced assets or pins. Failed multipart uploads are aborted best-effort; separately inspect incomplete uploads after interrupted processes.
