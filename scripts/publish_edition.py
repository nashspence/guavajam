#!/usr/bin/env python3
"""Stage verified edition files as a draft; publish only with explicit --publish."""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from asset_policy import AssetError
from verify_edition import verify_edition


def gh(*args, optional=False):
    result = subprocess.run(["gh", *args], text=True, capture_output=True)
    if result.returncode:
        if optional:
            return None
        raise AssetError("GitHub operation failed; no successful publication is claimed")
    return result.stdout


def release_records(repo):
    # Older supported gh versions emit concatenated JSON pages without --slurp.
    raw = gh("api", "--paginate", f"repos/{repo}/releases?per_page=100").lstrip()
    records = []
    decoder = json.JSONDecoder()
    while raw:
        page, end = decoder.raw_decode(raw)
        if not isinstance(page, list):
            raise AssetError("unexpected release-list response")
        records.extend(page)
        raw = raw[end:].lstrip()
    return records


def api(repo, path):
    return json.loads(gh("api", f"repos/{repo}/{path}"))


def tag_commit(repo, tag):
    result = subprocess.run(["gh", "api", f"repos/{repo}/git/ref/tags/{tag}"],
                            text=True, capture_output=True)
    if result.returncode:
        # Distinguish missing tags from network/auth failures.
        if "HTTP 404" in result.stderr:
            return None
        raise AssetError("cannot establish edition tag state")
    obj = json.loads(result.stdout)["object"]
    for _ in range(5):
        if obj["type"] == "commit":
            return obj["sha"]
        if obj["type"] != "tag":
            break
        obj = api(repo, "git/tags/" + obj["sha"])["object"]
    raise AssetError("edition tag does not resolve to a commit")


def require_source_check(repo, commit):
    compare = api(repo, f"compare/{commit}...main")
    if compare["status"] not in {"ahead", "identical"}:
        raise AssetError("reviewed source commit must belong to main history")
    checks = api(repo, f"commits/{commit}/check-runs?per_page=100")["check_runs"]
    for name in ["Validate manuscript sources", "Verify manuscript assets"]:
        matching = [check for check in checks if check["name"] == name
                    and check["app"]["slug"] == "github-actions"]
        # A historical success must not hide a later failed verification.
        if not matching or max(matching, key=lambda check: check["id"])["conclusion"] != "success":
            raise AssetError("reviewed commit requires successful " + name + " check")


def stage_edition(repo, directory, commit, tag, *, publish=False):
    manifest = verify_edition(directory, commit=commit, tag=tag)
    require_source_check(repo, commit)
    resolved = tag_commit(repo, tag)
    if resolved is not None and resolved != commit:
        raise AssetError("edition tag points at a different commit")
    # List releases through the API to distinguish missing releases from API errors.
    matches = [release for release in release_records(repo) if release["tag_name"] == tag]
    if matches and (not matches[0]["draft"] or matches[0]["prerelease"]):
        raise AssetError("will not modify an issued edition or prerelease")
    names = [manifest["canonical_artifact"], "provenance.json", "SHA256SUMS"]
    if not matches:
        notes = (f"Work: {manifest['title']}\nEdition: {tag.rsplit('/', 1)[1]}\n"
                 f"Canonical artifact: {manifest['canonical_artifact']}\nRights: {manifest['rights']}\n"
                 f"Source commit: {commit}\nProvenance: provenance.json\nChecksums: SHA256SUMS\n")
        with tempfile.TemporaryDirectory(prefix="press-notes-") as temp:
            note_file = Path(temp) / "notes.txt"
            note_file.write_text(notes, encoding="utf-8")
            gh("release", "create", tag, *(str(directory / name) for name in names),
               "--repo", repo, "--target", commit, "--draft", "--title",
               manifest["title"] + " — " + tag.rsplit("/", 1)[1], "--notes-file", str(note_file))
    else:
        release = matches[0]
        for label, expected in [("Work", manifest["title"]), ("Edition", tag.rsplit("/", 1)[1]),
                                ("Canonical artifact", manifest["canonical_artifact"]),
                                ("Rights", manifest["rights"]), ("Source commit", commit)]:
            if f"{label}: {expected}" not in release["body"].splitlines():
                raise AssetError("existing draft metadata differs from the verified edition")
        existing = {asset["name"] for asset in release["assets"]}
        missing = [str(directory / name) for name in names if name not in existing]
        if missing:
            gh("release", "upload", tag, *missing, "--repo", repo)
    with tempfile.TemporaryDirectory(prefix="press-draft-") as temp:
        for name in names:
            gh("release", "download", tag, "--repo", repo, "--pattern", name, "--dir", temp)
        verify_edition(Path(temp), commit=commit, tag=tag)
        if any((Path(temp) / name).read_bytes() != (directory / name).read_bytes() for name in names):
            raise AssetError("draft attachments differ from verified build; refusing publication")
    if tag_commit(repo, tag) != commit:
        raise AssetError("edition tag changed before publication")
    if publish:
        gh("release", "edit", tag, "--repo", repo, "--draft=false", "--prerelease=false")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "nashspence/press"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        parser.error("invalid repository")
    try:
        stage_edition(args.repo, args.directory, args.commit, args.tag, publish=args.publish)
    except (AssetError, OSError, ValueError, KeyError, TypeError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    print("Edition published" if args.publish else "Verified draft staged; not an official edition")
    return 0


if __name__ == "__main__":
    sys.exit(main())
