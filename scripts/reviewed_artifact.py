"""Retrieve or identify an exact reviewed proof from a successful staging run on main."""
import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from asset_policy import AssetError
from publish_edition import api, gh


def staging_builder(repo, run_id, digest):
    if not re.fullmatch(r"[1-9][0-9]*", run_id) or not re.fullmatch(r"[0-9a-f]{64}", digest):
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("download", "builder", "verify"):
        sub = commands.add_parser(command)
        sub.add_argument("run_id")
        sub.add_argument("provenance_sha256")
        if command != "builder":
            sub.add_argument("directory", type=Path)
    args = parser.parse_args()
    repo = os.environ["GITHUB_REPOSITORY"]
    try:
        if args.command == "download":
            download_reviewed(repo, args.run_id, args.provenance_sha256, args.directory)
        else:
            builder = staging_builder(repo, args.run_id, args.provenance_sha256)
            if args.command == "builder":
                print(builder)
            else:
                verify_reviewed(args.directory, args.provenance_sha256, builder)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        sys.exit(f"Reviewed artifact check failed: {exc}")
