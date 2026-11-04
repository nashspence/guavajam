# Assets and storage

Manuscripts are their own image inventory. The approved public origin is
**https://assets.press.nash.spence.0819870.xyz**, configured in
[asset-policy.json](asset-policy.json). There is no author-maintained asset manifest.

## Authoring and upload

Use a PNG, JPEG, WebP, or passive UTF-8 SVG, at most 10 MiB. Hash the **exact
uploaded bytes** with SHA-256. Upload under
sha256/<64-lowercase-hex>.(png|jpg|jpeg|webp|svg) with its correct Content-Type:
image/png, image/jpeg, image/webp, or image/svg+xml. SVG scripts, animation,
foreign elements, event handlers, style sheets, DTDs/entities and external
references are prohibited. Raster files must decode successfully, have at most
40 million pixels and contain one frame.

Set Cache-Control to **public, max-age=31536000, immutable** on the B2 object
(X-Bz-Info-b2-cache-control in the Native API). Percent-encode B2 custom
header values when using the Native API directly. Do not enable image optimization,
transcoding or content rewriting that changes the verified bytes. Never
overwrite, hide or delete a referenced object. Changed bytes get a new digest.

Before uploading, retain the original bytes in an independently backed-up
authoring location. Validate them with the same byte verifier used by the build:

~~~bash
mise install
mise x -- uv sync --locked
mise x -- uv run --locked python scripts/prepare_asset.py /path/to/figure.svg
~~~

Upload using the operator's B2 console or a bucket-restricted client, using the
printed object name and MIME type. [B2's upload API](https://www.backblaze.com/apidocs/b2-upload-file)
documents the upload headers. Keep storage credentials in the operator's secret
store; never put them in manuscripts, repository files, or PR/release workflows.

Use an ordinary Markdown image or reference definition:

~~~markdown
![Diagram explaining the argument][figure-1]

[figure-1]: https://assets.press.nash.spence.0819870.xyz/sha256/<actual-sha256>.svg
~~~

The placeholder above is illustrative. [The preview fixture](storage/README.md)
contains an exact content-addressed example that GitHub can render.

## Verification and publication

The **Validate manuscript sources** job checks Markdown and URL syntax offline.
The separate **Verify manuscript assets** job runs on **every push and pull
request** and downloads every referenced approved object, including reference-style
images. Both checks are required on main and before publication. Origin outages,
missing objects, redirects, wrong hashes, wrong MIME types, invalid image bytes,
and unsafe SVGs fail verification. This network check needs no storage credentials.

Downloads use verified TLS, no redirects, public IP addresses only, pinned DNS
results, a 30-second network timeout, streaming reads, a 10 MiB object limit and
a 100 MiB/128-image snapshot limit. No arbitrary external host is fetched.
Response content encoding must be identity; the advertised SHA-256 always denotes
the original object bytes.

The trusted [edition workflow](.github/workflows/prepare-edition.yml) downloads
and verifies assets again from the exact reviewed source snapshot. It rewrites
Pandoc's parsed image destinations in a temporary tree to verified local copies.
Only data URIs made from those copies reach the HTML renderer. Pandoc does not
fetch image URLs. MathML, inline CSS, responsive sizing and light/dark styling
are included in the canonical HTML.

The generated provenance.json records the source commit, intended tag, compiler,
canonical HTML digest, and each image's source URL, SHA-256, byte count, media
type and local build destination. SHA256SUMS covers the HTML and provenance file.
The package is checked offline before upload and downloaded from the draft for
verification again before publication. See [PUBLICATION.md](PUBLICATION.md).

## B2 and Cloudflare configuration

Use a dedicated **public B2 bucket**, with version history retained and no
lifecycle rule that hides or expires referenced objects. Content addressing is
an integrity convention, not an access-control or Object Lock mechanism.

The Object Lock policy is to make no WORM/retention claim and not change bucket
retention in this tooling. Retain all versions and never delete referenced
objects operationally. Enabling Object Lock or compliance retention requires
a separate operator decision; enabling the feature cannot be undone.
[Backblaze documents its retention behavior](https://www.backblaze.com/docs/cloud-storage-object-lock).

The serving path is a small [read-only Worker](storage/worker.js) on a Workers
Custom Domain. It accepts only GET/HEAD requests for content-addressed image paths
on the approved hostname and proxies them to one fixed public B2 bucket over HTTPS.
Redirects and failed upstream responses are not cached. Successful responses use
immutable caching and request no byte transformations. There is no edge hashing;
the required Actions checks verify every image.

Configure these operator-only deployment variables:

| Variable | Purpose |
| --- | --- |
| CLOUDFLARE_API_TOKEN | Account Workers Scripts edit and zone Workers Routes edit permissions |
| CLOUDFLARE_ACCOUNT_ID | Account hosting the Worker |
| CLOUDFLARE_ZONE_ID | Zone containing the approved asset hostname |
| B2_PUBLIC_BASE | B2 friendly HTTPS download base, ending in /file/<bucket> |

These are deployment inputs, not committed configuration. The backing bucket
and account details are not part of manuscript URLs.

~~~bash
mise x -- uv run --locked python scripts/deploy_asset_gateway.py
mise x -- uv run --locked python scripts/deploy_asset_gateway.py --apply
~~~

The first command prints a plan without API writes. The second uploads the
Worker with ASSET_HOST and B2_PUBLIC_BASE bindings and attaches the approved
hostname as a Custom Domain. It refuses to overwrite a Worker or Custom Domain
owned by another hostname/service. No B2 credential is needed in the Worker.

Cloudflare provisions a certificate for the exact hostname, including deeply
nested subdomains, without a separate Advanced Certificate Manager subscription.
[Cloudflare documents Custom Domain certificates](https://developers.cloudflare.com/workers/configuration/routing/custom-domains/#certificates).
Verify the certificate and public reads after deployment. Keep the public bucket
readable by GitHub's image proxy without signed or expiring URLs.

## Independent copies, audit and recovery

Every issued edition attaches its complete standalone HTML, provenance and
checksums to GitHub Releases. Embedded image bytes are an independent copy on a
different provider from B2. A released edition remains readable with B2 and
Cloudflare offline. Retain these three release files together in any additional
offline archive. GitHub Actions' temporary artifacts are not the durable archive.

[Audit asset integrity](.github/workflows/audit-assets.yml) runs monthly and can
also be dispatched manually. It verifies current referenced origin objects and
every official GitHub edition, checks tag/commit identity, rehashes embedded image
bytes, and verifies older editions' source objects. Failures require operator
investigation; the audit never repairs or publishes anything.

To recover an object, obtain a verified original, a retained B2 version, or decode
its image data URI from an edition after running scripts/verify_edition.py.
Recheck its digest against provenance and upload the exact bytes with the same
key and MIME/cache headers. Never repair a hash mismatch by changing a filename.

To migrate providers, copy every referenced object, preserving keys, bytes,
MIME and cache headers. Verify all copies against release provenance before
changing the approved hostname's backing DNS/routing. Keep the hostname and
/sha256/ URLs stable, retain the old provider through cutover, and rerun the
origin and edition audits. Do not rewrite issued editions.
