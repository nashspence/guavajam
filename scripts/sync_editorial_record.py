"""Publish the editorial record approved on main and read back the affected GitHub release."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.parse import quote

from asset_policy import AssetError
from identifier_schema import TAG


def affected_tags(changed):
    tags = set()
    for name in changed.decode("utf-8").split("\0"):
        if not name:
            continue
        path = Path(name)
        if len(path.parts) == 4 and path.parts[0] == "notices":
            tag = "/".join(("works", *path.parts[1:3]))
        elif len(path.parts) == 4 and path.parts[:2] == ("registrations", "editions") and path.suffix == ".json":
            tag = f"works/{path.parent.name}/{path.stem}"
        else:
            raise AssetError(f"unexpected editorial path: {path}")
        if not TAG.fullmatch(tag):
            raise AssetError(f"invalid edition tag at {path}")
        tags.add(tag)
    return sorted(tags)


def sync(root, repo, tag):
    from audit_assets import verify_public_release
    from build_edition import git
    from editorial_notices import edition_notices, release_notes
    from issuance import read_registration
    from publish_edition import api, gh, require_source_check
    from reader_routes import verify_reader_routes

    match = TAG.fullmatch(tag)
    if not match:
        raise AssetError("invalid edition tag")
    path = Path("registrations/editions") / match[1] / (match[2] + ".json")
    record = read_registration(root, path)
    if "published_at" not in record:
        print(f"{tag}: no publication receipt to synchronize")
        return
    release = api(repo, "releases/tags/" + quote(tag, safe=""))
    if (release["tag_name"] != tag or release["published_at"] != record["published_at"]
            or release["draft"] or release["prerelease"] or release.get("immutable") is not True):
        raise AssetError(f"{tag}: expected the receipted, immutable public release")
    with tempfile.TemporaryDirectory(prefix="press-notes-") as tmp:
        gh("release", "download", tag, "--repo", repo, "--pattern", "provenance.json", "--dir", tmp)
        raw = (Path(tmp) / "provenance.json").read_bytes()
        if hashlib.sha256(raw).hexdigest() != record["provenance_sha256"]:
            raise AssetError(f"{tag}: public provenance differs from receipt")
        manifest = json.loads(raw)
        notices = edition_notices(root, tag)
        body = release_notes(manifest, notices, identifiers=record.get("identifiers"))
        # Verify the full immutable package, its tag and its receipt before applying
        # the single proposed mutable field. This also catches an incorrect release title.
        verify_public_release(repo, {**release, "body": body}, root=root, require_receipt=True)
        if release["body"] != body:
            head = git(root, "rev-parse", "HEAD")
            require_source_check(repo, head, wait_seconds=600)
            if api(repo, "git/ref/heads/main")["object"]["sha"] != head:
                raise AssetError("main moved during verification; retry from current main")
            request = Path(tmp) / "body.json"
            request.write_text(json.dumps({"body": body}), encoding="utf-8")
            gh("api", "--method", "PATCH", f"repos/{repo}/releases/{release['id']}", "--input", str(request))
    public = api(repo, f"releases/{release['id']}")
    if public["id"] != release["id"] or public["tag_name"] != tag or public["body"] != body:
        raise AssetError(f"{tag}: public editorial record differs after synchronization")
    # GitHub's API and the reader-facing page may converge a few seconds apart.
    for delay in (0, 5, 15):
        if delay:
            time.sleep(delay)
        try:
            verify_reader_routes(manifest, repo, notices=notices, identifiers=record.get("identifiers"),
                                 published_at=record["published_at"])
            break
        except (OSError, ValueError):
            if delay == 15:
                raise
    print(f"{tag}: verified current notes at the public record")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_mutually_exclusive_group(required=True)
    commands.add_argument("--changed", action="store_true", help="select editions from NUL-terminated changed paths on stdin")
    commands.add_argument("--select", help="validate one edition tag for a manual retry")
    commands.add_argument("--sync", help="synchronize one receipted edition on main")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        if args.changed:
            print(json.dumps(affected_tags(sys.stdin.buffer.read())))
        elif args.select is not None:
            if not TAG.fullmatch(args.select):
                raise AssetError("invalid edition tag")
            print(json.dumps([args.select]))
        else:
            repo = os.environ["GITHUB_REPOSITORY"]
            if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
                raise AssetError("invalid repository")
            sync(root, repo, args.sync)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        sys.exit(f"Editorial synchronization failed: {exc}")


if __name__ == "__main__":
    main()
