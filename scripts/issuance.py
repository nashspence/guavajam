"""Record and verify the issuer's independent commitment to a reviewed edition."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

from asset_policy import AssetError
from editorial_notices import release_notes
from identifier_schema import REGISTRY_SCHEMAS, TAG, edition_release_url, strict_json
from verify_edition import verify_edition


def authorization(directory: Path, manifest: dict) -> dict:
    tag = manifest["tag"]
    match = TAG.fullmatch(tag)
    if not match:
        raise AssetError("invalid issuance tag")
    slug, version = match.groups()
    return {
        "schema": REGISTRY_SCHEMAS["edition"], "work": slug, "version": version,
        "release_url": edition_release_url(tag),
        "canonical_artifact": manifest["canonical_artifact"], "rights": manifest["rights"],
        "artifact_sha256": manifest["html_sha256"], "source_commit": manifest["source_commit"],
        "initial_notes_sha256": hashlib.sha256(release_notes(manifest).encode("utf-8")).hexdigest(),
        "provenance_sha256": hashlib.sha256((directory / "provenance.json").read_bytes()).hexdigest(),
    }


def read_registration(root: Path, path: Path) -> dict:
    file = root / path
    if not file.is_file() or file.resolve() != file.absolute():
        raise AssetError(f"missing issuance record {path}")
    record, errors = strict_json(file.read_bytes(), path)
    if errors:
        raise AssetError("\n".join(errors))
    return record


def check_issuance(root: Path, directory: Path, manifest: dict, *, published_at=None,
                   pending=False, require_receipt=False) -> dict:
    """Read the issuer checkout's independent record; the manuscript snapshot can be older."""
    expected = authorization(directory, manifest)
    tag = manifest["tag"]
    path = Path("registrations/editions") / expected["work"] / (expected["version"] + ".json")
    record = read_registration(root, path)
    if pending and "published_at" in record:
        raise AssetError(f"{tag}: issued edition reverted to draft")
    for key, value in expected.items():
        if record.get(key) != value:
            raise AssetError(f"{tag}: issuance record {key} differs from the reviewed package")
    if require_receipt and "published_at" not in record:
        raise AssetError(f"{tag}: record the public release timestamp {published_at} in {path}")
    if published_at is not None and record.get("published_at") not in (None, published_at):
        raise AssetError(f"{tag}: issuance record published_at differs from public release")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare",))
    parser.add_argument("directory", type=Path, help="directory containing a verified edition")
    args = parser.parse_args()
    try:
        manifest = verify_edition(args.directory)
        record = authorization(args.directory, manifest)
    except (AssetError, OSError, ValueError, KeyError, TypeError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    print(json.dumps(record, sort_keys=True, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
