#!/usr/bin/env python3
"""Publish an authorized edition after verifying its GitHub draft and public bytes."""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from asset_policy import AssetError
from build_edition import COMMIT
from issuance import check_issuance
from editorial_notices import edition_fields, edition_notices, release_notes
from verify_edition import verify_edition


def gh(*args, optional=False):
    result = subprocess.run(["gh", *args], text=True, capture_output=True)
    if result.returncode:
        if optional:
            return None
        # Show the operation and HTTP status without logging release notes, signed
        # URLs, or other arguments and response text.
        operation = args[0]
        if args[:2] == ("api", "--method"):
            operation += " " + args[2]
        elif args[0] in {"release", "run"}:
            operation += " " + args[1]
        status = re.search(r"\bHTTP [1-5][0-9]{2}\b", result.stderr)
        detail = status[0] if status else f"exit {result.returncode}"
        raise AssetError(f"gh {operation} failed ({detail})")
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


def release_title(manifest):
    return manifest["title"] + " — " + edition_fields(manifest)["Edition"]


def release_inventory(release, tag, manifest, names, *, draft, notices=(), identifiers=None):
    if release["tag_name"] != tag or release["draft"] != draft or release["prerelease"]:
        raise AssetError("unexpected edition release state")
    if release.get("name") != release_title(manifest):
        raise AssetError("release title differs from the approved edition")
    if not draft and release.get("immutable") is not True:
        raise AssetError("published edition is not an immutable GitHub release")
    if release["body"] != release_notes(manifest, notices, identifiers=identifiers):
        raise AssetError("release notes differ from the approved edition, identifiers, and notices")
    uploaded = {asset["name"]: asset["id"] for asset in release["assets"]
                if asset["state"] == "uploaded"}
    if len(uploaded) != len(release["assets"]) or set(uploaded) - set(names):
        raise AssetError("unexpected or incomplete release attachment")
    return uploaded


def check_release_target(release, resolved, commit):
    if resolved is None and release["draft"]:
        if release["target_commitish"] != commit:
            raise AssetError("untagged draft must target the reviewed commit SHA")
    elif resolved != commit:
        raise AssetError("edition tag does not point at the reviewed commit")


def verify_remote(repo, release_id, directory, commit, tag, manifest, names, *, draft, notices=(),
                  identifiers=None, expected_inventory=None):
    def read_inventory():
        release = api(repo, f"releases/{release_id}")
        inventory = release_inventory(release, tag, manifest, names, draft=draft, notices=notices,
                                      identifiers=identifiers)
        if expected_inventory is not None and inventory != expected_inventory:
            raise AssetError("edition attachments changed during verification")
        if expected_inventory is None and set(inventory) != set(names):
            raise AssetError("missing release attachments")
        check_release_target(release, tag_commit(repo, tag), commit)
        return inventory

    inventory = read_inventory()
    with tempfile.TemporaryDirectory(prefix="press-release-") as temp:
        for name in inventory:
            gh("release", "download", tag, "--repo", repo, "--pattern", name, "--dir", temp)
            if (Path(temp) / name).read_bytes() != (directory / name).read_bytes():
                raise AssetError("release attachments differ from verified build")
    if read_inventory() != inventory:
        raise AssetError("edition attachments changed during verification")


def publish_edition(repo, directory, commit, tag, *, issuer_commit):
    if not COMMIT.fullmatch(issuer_commit or ""):
        raise AssetError("promotion requires the full issuer commit SHA")
    manifest = verify_edition(directory, commit=commit, tag=tag)
    root = Path(__file__).resolve().parents[1]
    require_source_check(repo, commit)
    if issuer_commit != commit:
        require_source_check(repo, issuer_commit)
    resolved = tag_commit(repo, tag)
    if resolved is not None and resolved != commit:
        raise AssetError("edition tag points at a different commit")
    matches = [release for release in release_records(repo) if release["tag_name"] == tag]
    if len(matches) > 1:
        raise AssetError("multiple releases use the edition tag")
    fields = edition_fields(manifest)
    names = [fields["Canonical artifact"], fields["Provenance"], fields["Checksums"]]
    release = matches[0] if matches else None
    record = check_issuance(root, directory, manifest, pending=release is None or release["draft"],
                            published_at=release.get("published_at") if release else None)
    if release is not None and not release["draft"]:
        verify_remote(repo, release["id"], directory, commit, tag, manifest, names, draft=False,
                      notices=edition_notices(root, tag), identifiers=record.get("identifiers"))
        return manifest

    def require_current_issuer():
        if api(repo, "git/ref/heads/main")["object"]["sha"] != issuer_commit:
            raise AssetError("main moved; re-dispatch promotion using the same reviewed staging run")

    require_current_issuer()
    if release is None:
        release = json.loads(gh("api", "--method", "POST", f"repos/{repo}/releases",
                                "-f", "tag_name=" + tag, "-f", "target_commitish=" + commit,
                                "-F", "draft=true", "-f", "name=" + release_title(manifest),
                                "-f", "body=" + release_notes(manifest)))
    inventory = release_inventory(release, tag, manifest, names, draft=True)
    check_release_target(release, resolved, commit)
    missing = [str(directory / name) for name in names if name not in inventory]
    release_id = release["id"]
    if missing:
        if inventory:
            verify_remote(repo, release_id, directory, commit, tag, manifest, names, draft=True,
                          expected_inventory=inventory)
        gh("release", "upload", tag, *missing, "--repo", repo)
    verify_remote(repo, release_id, directory, commit, tag, manifest, names, draft=True)
    require_current_issuer()
    # Publishing materializes a fresh tag from target_commitish. Bind both the
    # release ID and the target in this request, then check the public result.
    try:
        gh("api", "--method", "PATCH", f"repos/{repo}/releases/{release_id}",
           "-F", "draft=false", "-F", "prerelease=false", "-f", "target_commitish=" + commit,
           "-f", "body=" + release_notes(manifest))
    except (AssetError, OSError) as request_error:
        # Never repeat a write with an unknown outcome. Inspect the same release ID.
        try:
            state = api(repo, f"releases/{release_id}")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise AssetError(f"publication outcome unknown for release {release_id}: {exc}") from request_error
        if state.get("draft") is True:
            raise AssetError(f"release {release_id} remains a draft after failed promotion") from request_error
    try:
        verify_remote(repo, release_id, directory, commit, tag, manifest, names, draft=False)
    except (AssetError, OSError, ValueError, KeyError, TypeError) as exc:
        raise AssetError(f"publication submitted for release {release_id}; inspect public edition: {exc}") from exc
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "nashspence/press"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--issuer-commit", required=True, help="Promotion workflow dispatch commit")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        parser.error("invalid repository")
    try:
        publish_edition(args.repo, args.directory, args.commit, args.tag, issuer_commit=args.issuer_commit)
    except (AssetError, OSError, ValueError, KeyError, TypeError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    print("Edition published")
    return 0


if __name__ == "__main__":
    sys.exit(main())
