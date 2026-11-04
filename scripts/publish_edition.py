#!/usr/bin/env python3
"""Publish an authorized edition after verifying its GitHub draft and public bytes."""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from asset_policy import AssetError
from build_edition import COMMIT
from issuance import check_issuance
from editorial_notices import edition_notices, release_notes
from verify_edition import verify_edition


def gh(*args, optional=False, token=None):
    env = {**os.environ, "GH_TOKEN": token} if token is not None else None
    result = subprocess.run(["gh", *args], text=True, capture_output=True, env=env)
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


def require_source_check(repo, commit, *, wait_seconds=0):
    compare = api(repo, f"compare/{commit}...main")
    if compare["status"] not in {"ahead", "identical"}:
        raise AssetError("reviewed source commit must belong to main history")
    workflow = "validate-source.yml"
    runs_path = f"actions/workflows/{workflow}/runs?event=push&branch=main&head_sha={commit}&per_page=100"
    deadline = time.monotonic() + wait_seconds
    while True:
        data = api(repo, runs_path)
        runs = data["workflow_runs"]
        if data["total_count"] != len(runs):
            raise AssetError("cannot establish latest main push validation from an incomplete run list")
        if runs:
            run = max(runs, key=lambda item: item["id"])
            if (run["path"] != ".github/workflows/" + workflow or run["event"] != "push"
                    or run["head_repository"]["full_name"] != repo or run["head_branch"] != "main"
                    or run["head_sha"] != commit):
                raise AssetError("unexpected main push validation identity for the reviewed commit")
            if run["status"] == "completed":
                if run["conclusion"] != "success":
                    raise AssetError("latest main push validation must have succeeded for the reviewed commit")
                break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssetError("expected completed main push validation for the reviewed commit")
        time.sleep(min(15, remaining))
    jobs = api(repo, f'actions/runs/{run["id"]}/attempts/{run["run_attempt"]}/jobs?per_page=100')["jobs"]
    for name in ["Validate manuscript sources", "Verify manuscript assets"]:
        matching = [job for job in jobs if job["name"] == name]
        if len(matching) != 1 or matching[0]["conclusion"] != "success":
            raise AssetError(f"latest validation attempt requires successful {name}; re-run all jobs")
        if name == "Validate manuscript sources" and not any(
                step["name"] == "Verify editorial history against the event base" and step["conclusion"] == "success"
                for step in matching[0]["steps"]):
            raise AssetError("the main push did not verify editorial history against its previous tip")


def require_immutable_releases(repo, policy_token):
    if not policy_token:
        raise AssetError("press-release requires PRESS_POLICY_READ_TOKEN to verify release immutability")
    try:
        policy = json.loads(gh("api", f"repos/{repo}/immutable-releases", token=policy_token))
    except AssetError as exc:
        raise AssetError(f"cannot confirm repository release immutability: {exc}") from exc
    if policy.get("enabled") is not True:
        raise AssetError("enable immutable releases before publishing this draft")


def release_title(manifest):
    return manifest["title"] + " — " + manifest["tag"].rsplit("/", 1)[1]


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


def publish_edition(repo, directory, commit, tag, *, issuer_commit, policy_token=None):
    if not COMMIT.fullmatch(issuer_commit or ""):
        raise AssetError("promotion requires the full issuer commit SHA")
    manifest = verify_edition(directory, commit=commit, tag=tag)
    root = Path(__file__).resolve().parents[1]
    for sha in dict.fromkeys([commit, manifest["builder_commit"], issuer_commit]):
        require_source_check(repo, sha)
    resolved = tag_commit(repo, tag)
    if resolved is not None and resolved != commit:
        raise AssetError("edition tag points at a different commit")
    matches = [release for release in release_records(repo) if release["tag_name"] == tag]
    if len(matches) > 1:
        raise AssetError("multiple releases use the edition tag")
    names = [manifest["canonical_artifact"], "provenance.json", "SHA256SUMS"]
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
    require_immutable_releases(repo, policy_token)
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
            raise AssetError(f"release {release_id} remains a draft after failed promotion: {request_error}") from request_error
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
        policy_token = os.environ.pop("PRESS_POLICY_READ_TOKEN", None)
        publish_edition(args.repo, args.directory, args.commit, args.tag, issuer_commit=args.issuer_commit,
                        policy_token=policy_token)
    except (AssetError, OSError, ValueError, KeyError, TypeError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    print("Edition published")
    return 0


if __name__ == "__main__":
    sys.exit(main())
