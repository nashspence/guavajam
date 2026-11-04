"""Check that the profile files used by each official edition still match its provenance."""
import json
import os
import sys
from pathlib import Path

from asset_policy import AssetError
from build_edition import TAG
from publish_edition import gh, release_records
from render_profile import digest, profile_paths


def check_profile_history(root: Path, repo: str) -> None:
    root = root.resolve()
    for release in release_records(repo):
        tag = release["tag_name"]
        if release["draft"] or release["prerelease"] or not TAG.fullmatch(tag):
            continue

        try:
            raw = gh("release", "download", tag, "--repo", repo, "--pattern", "provenance.json",
                     "--output", "-")
            manifest = json.loads(raw)
            if manifest["tag"] != tag:
                raise AssetError("provenance names a different edition")
            template, stylesheet = profile_paths(manifest["presentation_profile"])
            for field, relative in (("template", template), ("stylesheet", stylesheet)):
                if manifest[field + "_path"] != relative:
                    raise AssetError(f"unexpected {field} path in provenance")
                path = root / relative
                if (path.resolve() != path or not path.is_file()
                        or digest(path.read_bytes()) != manifest[field + "_sha256"]):
                    raise AssetError(f"restore {relative} and use a new profile for changed bytes")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise AssetError(f"{tag}: {exc}") from exc


if __name__ == "__main__":
    try:
        check_profile_history(Path(__file__).resolve().parents[1], os.environ["GITHUB_REPOSITORY"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        sys.exit(f"Edition profile check failed: {exc}")
