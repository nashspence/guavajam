"""Check that claimed and candidate editions agree with the checkout's profile files."""
import argparse
import json
import sys
from pathlib import Path

from asset_policy import AssetError
from public_record import REGISTRATIONS, claimed_registrations, saved_manifest, tracked_paths
from render_profile import digest, profile_paths


def check_profile_files(root: Path, manifest: dict) -> None:
    template, stylesheet = profile_paths(manifest["presentation_profile"])
    for field, relative in (("template", template), ("stylesheet", stylesheet)):
        if manifest[field + "_path"] != relative:
            raise AssetError(f"unexpected {field} path in provenance")
        path = root / relative
        if (path.resolve() != path or not path.is_file()
                or digest(path.read_bytes()) != manifest[field + "_sha256"]):
            raise AssetError(f"restore {relative} and use a new profile for changed bytes")


def check_profile_history(root: Path, *, candidate: Path | None = None) -> None:
    root = root.resolve()
    if candidate is not None:
        check_profile_files(root, json.loads(candidate.read_bytes()))
    for slug, version, record in claimed_registrations(root, tracked_paths(root, REGISTRATIONS)):
        check_profile_files(root, saved_manifest(root, slug, version, record))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, help="Also check a staged edition's provenance.json")
    args = parser.parse_args()
    try:
        check_profile_history(Path(__file__).resolve().parents[1], candidate=args.candidate)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        sys.exit(f"Edition profile check failed: {exc}")
