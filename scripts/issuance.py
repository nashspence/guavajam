"""Record and verify the issuer's independent commitment to a reviewed edition."""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

from asset_policy import AssetError
from editorial_notices import reviewed_initial_body
from identifier_schema import REGISTRY_SCHEMAS, STAGING_RUN_ID, TAG, strict_json, valid_utc
from verify_edition import verify_edition


def authorization(directory: Path, manifest: dict, *, staged_run_id: str | None = None) -> dict:
    tag = manifest["tag"]
    match = TAG.fullmatch(tag)
    if not match:
        raise AssetError("invalid issuance tag")
    slug, version = match.groups()
    record = {
        "schema": REGISTRY_SCHEMAS["edition"], "work": slug, "version": version,
        "record_url": manifest["record_url"],
        "canonical_artifact": manifest["canonical_artifact"], "rights": manifest["rights"],
        "artifact_sha256": manifest["html_sha256"], "source_commit": manifest["source_commit"],
        "initial_notes_sha256": hashlib.sha256(reviewed_initial_body(manifest).encode("utf-8")).hexdigest(),
        "provenance_sha256": hashlib.sha256((directory / "provenance.json").read_bytes()).hexdigest(),
    }
    if staged_run_id is not None:
        if not STAGING_RUN_ID.fullmatch(staged_run_id):
            raise AssetError("expected a positive decimal staging run ID")
        record["staged_run_id"] = staged_run_id
    return record


def read_registration(root: Path, path: Path) -> dict:
    file = root / path
    if not file.is_file() or file.resolve() != file.absolute():
        raise AssetError(f"missing issuance record {path}")
    record, errors = strict_json(file.read_bytes(), path)
    if errors:
        raise AssetError("\n".join(errors))
    return record


def check_authorization(record: dict, directory: Path, manifest: dict, *, require_claim=False) -> None:
    """Compare a separately trusted registration with the edition's commitments."""
    if "cancellation_reason" in record:
        raise AssetError(f"{manifest['tag']}: issuance intent was cancelled; this version is retired")
    if (require_claim or "published_at" in record or "issuing" in record) and record.get("issuing") is not True:
        raise AssetError(f"{manifest['tag']}: merge the issuing claim before promotion")
    for key, value in authorization(directory, manifest).items():
        if record.get(key) != value:
            raise AssetError(f"{manifest['tag']}: issuance record {key} differs from the reviewed package")


def check_issuance(root: Path, directory: Path, manifest: dict, *, published_at=None,
                   pending=False, require_receipt=False) -> dict:
    """Read the issuer checkout's independent record; the manuscript snapshot can be older."""
    tag = manifest["tag"]
    match = TAG.fullmatch(tag)
    if not match:
        raise AssetError("invalid issuance tag")
    path = Path("registrations/editions") / match[1] / (match[2] + ".json")
    record = read_registration(root, path)
    if pending and "published_at" in record:
        raise AssetError(f"{tag}: issued edition reverted to draft")
    check_authorization(record, directory, manifest, require_claim=pending or published_at is not None or require_receipt)
    if require_receipt and "published_at" not in record:
        raise AssetError(f"{tag}: record the public release timestamp {published_at} in {path}")
    if published_at is not None and record.get("published_at") not in (None, published_at):
        raise AssetError(f"{tag}: issuance record published_at differs from public release")
    return record


def public_receipt(root: Path, directory: Path, manifest: dict, repo: str) -> dict:
    """Produce a commit-ready receipt only after verifying this exact public edition."""
    from audit_assets import verify_public_release
    from publish_edition import api

    tag = manifest["tag"]
    release = api(repo, "releases/tags/" + quote(tag, safe=""))
    if release.get("tag_name") != tag or not valid_utc(release.get("published_at")):
        raise AssetError(f"{tag}: expected the official release and its exact UTC publication timestamp")
    verify_public_release(repo, release, root=root)
    timestamp = release["published_at"]
    record = check_issuance(root, directory, manifest, published_at=timestamp)
    return {**record, "published_at": timestamp}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "receipt"))
    parser.add_argument("directory", type=Path, help="directory containing a verified edition")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "nashspence/press"))
    parser.add_argument("--staged-run-id", help="successful hosted build supplying this authorization")
    args = parser.parse_args()
    try:
        manifest = verify_edition(args.directory)
        if args.command == "receipt":
            record = public_receipt(Path(__file__).resolve().parents[1], args.directory, manifest, args.repo)
        else:
            record = authorization(args.directory, manifest, staged_run_id=args.staged_run_id)
    except (AssetError, OSError, ValueError, KeyError, TypeError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    print(json.dumps(record, sort_keys=True, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
