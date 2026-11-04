"""Retrieve the reviewed edition from a successful staging run on main."""
import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from asset_policy import AssetError
from publish_edition import api, gh


def download_reviewed(repo, run_id, digest, directory):
    if not re.fullmatch(r"[1-9][0-9]*", run_id) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise AssetError("promotion requires a staging run ID and the reviewed provenance SHA-256")
    run = api(repo, f"actions/runs/{run_id}")
    if (run["path"] != ".github/workflows/prepare-edition.yml"
            or run["head_repository"]["full_name"] != repo or run["head_branch"] != "main"
            or run["event"] != "workflow_dispatch" or run["conclusion"] != "success"):
        raise AssetError("expected a successful edition preparation run dispatched on main")
    if directory.exists():
        raise AssetError("reviewed artifact destination must be new")
    gh("run", "download", run_id, "--repo", repo, "--name", "verified-edition",
       "--dir", str(directory))
    raw = (directory / "provenance.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise AssetError("downloaded provenance differs from the reviewed digest")
    if json.loads(raw)["builder_commit"] != run["head_sha"]:
        raise AssetError("edition builder differs from the staging run commit")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("provenance_sha256")
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        download_reviewed(os.environ["GITHUB_REPOSITORY"], args.run_id, args.provenance_sha256,
                          args.directory)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        sys.exit(f"Reviewed artifact check failed: {exc}")
