"""Retrieve an edition's preserved attachments directly from a recovery host."""
import hashlib
from pathlib import Path
import tempfile

from asset_policy import AssetError, MAX_HTML_BYTES, public_response, response_chunks, download_bytes
from deploy_record_gateway import target_base
from identifier_schema import TAG
from issuance import check_authorization
from verify_edition import verify_edition
from edition_package import package_names, bundle_limit, MAX_SUMS_BYTES


def verify_backup_package(base, tag, receipt):
    """Check fresh, redirect-free downloads against the independently retained commitments."""
    base = target_base(base)
    match = TAG.fullmatch(tag)
    if not match:
        raise AssetError("invalid edition tag")
    filename = match[1] + "-" + match[2] + ".html"
    files = [(filename, receipt["artifact_sha256"], MAX_HTML_BYTES),
             ("provenance.json", receipt["provenance_sha256"], MAX_HTML_BYTES),
             ("SHA256SUMS", None, MAX_SUMS_BYTES)]
    with tempfile.TemporaryDirectory(prefix="press-backup-") as temp:
        directory = Path(temp)
        for name, expected, limit in files:
            url = base + "/" + tag + "/" + name
            digest = hashlib.sha256()
            with public_response(url, "*/*", timeout=120) as response:
                if response.status != 200:
                    raise AssetError(f"{url}: expected backup file (HTTP {response.status})")
                with (directory / name).open("xb") as output:
                    for chunk in response_chunks(response, limit):
                        output.write(chunk)
                        digest.update(chunk)
            # verify_edition checks the complete checksum inventory.
            if expected is not None and digest.hexdigest() != expected:
                raise AssetError(f"{url}: backup file differs from the trusted receipt")
        import json
        saved=json.loads((directory/'provenance.json').read_bytes())
        for name in package_names(saved)[3:]:
            with public_response(base+'/'+tag+'/'+name,'application/octet-stream',timeout=120) as response:
                if response.status!=200:raise AssetError('missing supplementary backup package')
                raw=b''.join(response_chunks(response,bundle_limit(saved)))
            (directory/name).write_bytes(raw)
        manifest = verify_edition(directory, tag=tag)
        check_authorization(receipt, directory, manifest)
        # Check the standby objects used by the permanent asset gateway as well.
        for _ in download_bytes(manifest, base=base):
            pass
        return manifest
