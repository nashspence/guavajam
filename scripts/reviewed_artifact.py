"""Retrieve or identify an exact reviewed proof from a successful staging run on main."""
import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from asset_policy import AssetError
from identifier_schema import STAGING_RUN_ID, TAG
from issuance import check_authorization, read_registration
from public_record import EDITION_RECORDS, REGISTRATIONS, saved_manifest
from publish_edition import api, gh
from verify_edition import verify_edition
from edition_package import package_names


def staging_builder(repo, run_id, digest):
    if not STAGING_RUN_ID.fullmatch(run_id) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise AssetError("promotion requires a staging run ID and the reviewed provenance SHA-256")
    run = api(repo, f"actions/runs/{run_id}")
    if (run["path"] != ".github/workflows/prepare-edition.yml"
            or run["head_repository"]["full_name"] != repo or run["head_branch"] != "main"
            or run["event"] != "workflow_dispatch" or run["conclusion"] != "success"):
        raise AssetError("expected a successful edition preparation run dispatched on main")
    builder = run["head_sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", builder):
        raise AssetError("expected full staging builder commit SHA")
    jobs = api(repo, f"actions/runs/{run_id}/jobs?per_page=100")["jobs"]
    if not any(job["name"] == "build" and job["conclusion"] == "success" for job in jobs):
        raise AssetError("expected a successful proof build in the original staging run")
    return builder


def download_reviewed(repo, run_id, digest, directory):
    builder = staging_builder(repo, run_id, digest)
    if directory.exists():
        raise AssetError("reviewed artifact destination must be new")
    gh("run", "download", run_id, "--repo", repo, "--name", "verified-edition",
       "--dir", str(directory))
    verify_reviewed(directory, digest, builder)


def verify_reviewed(directory, digest, builder):
    raw = (directory / "provenance.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise AssetError("proof provenance differs from the reviewed digest")
    if json.loads(raw)["builder_commit"] != builder:
        raise AssetError("edition builder differs from the staging run commit")


def restore_claimed(root, repo, tag, run_id, digest, directory):
    """Read saved attachments as data. The protected claim supplies their identity."""
    match = TAG.fullmatch(tag)
    if not match or not STAGING_RUN_ID.fullmatch(run_id) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise AssetError("expected an edition tag, original staging run ID and provenance SHA-256")
    slug, version = match.groups()
    registration = read_registration(root, REGISTRATIONS / slug / (version + ".json"))
    if (registration.get("issuing") is not True or "cancellation_reason" in registration
            or registration.get("staged_run_id") != run_id or registration["provenance_sha256"] != digest):
        raise AssetError("saved proof must match the original staging run and digest of an issuing claim")
    manifest = saved_manifest(root, slug, version, registration)

    transport = "proof-restore-" + digest
    draft = json.loads(gh("release", "view", transport, "--repo", repo, "--json", "isDraft,assets"))
    if draft["isDraft"] is not True:
        raise AssetError("saved proof transport must be an unpublished draft")
    filename = slug + "-" + version + ".html"
    provenance_size = (root / EDITION_RECORDS / slug / version / "provenance.json").stat().st_size
    limits = {filename: manifest["html_byte_count"], "provenance.json": provenance_size, "SHA256SUMS": 4096}
    if 'supplementary_package' in manifest:
        bundle=manifest['supplementary_package'];package_names(manifest)
        limits[bundle['filename']]=bundle['byte_count']
    assets = draft["assets"]
    if (len(assets) != len(limits) or {a["name"] for a in assets} != limits.keys()
            or any(a["state"] != "uploaded" or not 0 < a["size"] <= limits[a["name"]] for a in assets)):
        raise AssetError("saved proof draft requires exactly the bounded, reviewed attachments")
    directory.mkdir(parents=True, exist_ok=False)
    patterns=[item for name in limits for item in ('--pattern',name)]
    gh("release", "download", transport, "--repo", repo, "--dir", str(directory), *patterns)
    if any((directory / name).stat().st_size > limit for name, limit in limits.items()):
        raise AssetError("saved proof exceeds the claimed file size")
    verify_reviewed(directory, digest, manifest["builder_commit"])
    proof = verify_edition(directory, tag=tag, commit=registration["source_commit"])
    check_authorization(registration, directory, proof, require_claim=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("download", "builder", "verify"):
        sub = commands.add_parser(command)
        sub.add_argument("run_id")
        sub.add_argument("provenance_sha256")
        if command != "builder":
            sub.add_argument("directory", type=Path)
    restore = commands.add_parser("restore")
    restore.add_argument("run_id")
    restore.add_argument("provenance_sha256")
    restore.add_argument("directory", type=Path)
    restore.add_argument("--tag", required=True)
    args = parser.parse_args()
    repo = os.environ["GITHUB_REPOSITORY"]
    try:
        if args.command == "download":
            download_reviewed(repo, args.run_id, args.provenance_sha256, args.directory)
        elif args.command == "restore":
            restore_claimed(Path(__file__).resolve().parents[1], repo, args.tag, args.run_id,
                            args.provenance_sha256, args.directory)
        else:
            builder = staging_builder(repo, args.run_id, args.provenance_sha256)
            if args.command == "builder":
                print(builder)
            else:
                verify_reviewed(args.directory, args.provenance_sha256, builder)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        sys.exit(f"Reviewed artifact check failed: {exc}")
