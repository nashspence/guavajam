# Manuscript images

## Upload an image

Keep an independently backed-up copy of the original. Use `make help` from the
repository root for the commands used in this guide. Prepare the image to get
the B2 object key and HTTP headers for its exact bytes.

Upload the original with the printed key, Content-Type and Cache-Control. Use
the B2 console or a bucket-restricted client. With the native API, set
`X-Bz-Info-b2-cache-control` and percent-encode the custom header value.
Keep upload credentials in the operator's secret store. [B2 upload API](https://www.backblaze.com/apidocs/b2-upload-file)

Append the printed key to the public origin and use the resulting HTTPS URL in
a Markdown image with descriptive alt text. Changed bytes get a new key so
existing manuscripts still display the original. [Approved origin](asset-policy.json) · [Live Markdown example](storage/README.md)

## Configure the gateway

Use a dedicated public B2 bucket. Retain all versions and disable lifecycle rules
that hide or expire referenced objects. Preserve the original bytes through
delivery by disabling image optimization and transcoding. Decide separately
whether to enable Object Lock. [B2 Object Lock](https://www.backblaze.com/docs/cloud-storage-object-lock)

Set `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_ZONE_ID`, `CLOUDFLARE_API_TOKEN`
and `B2_PUBLIC_BASE` in the operator environment. The token needs Workers Scripts
edit access on the account and Workers Routes edit access on the zone. Use the
bucket's public HTTPS download base ending in `/file/<bucket>`.

Review the gateway deployment plan before applying it. Open the live preview over HTTPS
to check the certificate and anonymous image access. [Preview](storage/README.md)

## Recover or migrate images

Archive each edition's HTML, provenance and checksums together. To restore a
missing image, retrieve the original or a retained B2 version. You can also
extract the image's data URI from a released HTML file after verifying its
downloaded package.

Compare the recovered bytes' SHA-256 with the digest in the original URL.
Upload matching bytes under the original key with the original headers.
Investigate mismatches using a different copy.

To move providers, copy the objects referenced by current manuscripts **and past
editions**. Verify the digests and headers before routing the existing hostname
to the new origin. Keep the old origin available through cutover and rerun the
[integrity audit](.github/workflows/audit-assets.yml).
