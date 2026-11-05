#!/usr/bin/env python3
"""Dispatch trusted asset maintenance, or retire an exact branch tip in Actions."""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

from asset_policy import AssetError
from asset_storage import B2, credentials
from manage_assets import SHA, collect, git, operator_lock, pages, repository, require_action, retained_assets
from publish_edition import api, gh


def branch_name(root, branch):
    result = subprocess.run(["git", "check-ref-format", "--branch", branch], cwd=root, capture_output=True)
    if (result.returncode or not branch or branch.startswith("-") or result.stdout.strip() != branch.encode()
            or branch == "main"):
        raise AssetError("provide a valid feature branch; main cannot be retired")
    return branch


def request(root, command, env):
    repo = repository(root)
    if command == "request-gc":
        apply = env.get("APPLY", "")
        if apply not in {"", "0", "1"}:
            raise AssetError("APPLY must be 0 or 1")
        fields = {"operation": "sweep" if apply == "1" else "preview"}
    else:
        branch = branch_name(root, env.get("BRANCH", ""))
        sha = env.get("HEAD_SHA", "")
        if not SHA.fullmatch(sha):
            raise AssetError("HEAD_SHA must be the full reviewed 40-character branch tip")
        fields = {"operation": "retire", "branch": branch, "head_sha": sha}
    # JSON stdin keeps branch names as data and never selects a feature workflow.
    result = subprocess.run(["gh", "api", "--method", "POST",
                             f"repos/{repo}/actions/workflows/maintain-assets.yml/dispatches", "--input", "-"],
                            input=json.dumps({"ref": "main", "inputs": fields}), text=True, capture_output=True)
    if result.returncode:
        raise AssetError("cannot dispatch asset maintenance on main; check workflow setup and gh authentication")
    print("Requested", fields["operation"], "at https://github.com/" + repo + "/actions/workflows/maintain-assets.yml")


def summary(record):
    print(json.dumps(record, sort_keys=True))
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as stream:
            # JSON escaping does not escape Markdown fences; HTML-escape instead.
            import html
            stream.write("<pre>" + html.escape(json.dumps(record, indent=2)) + "</pre>\n")


def retire(root, repo, branch, expected, token):
    branch_name(root, branch)
    if not SHA.fullmatch(expected):
        raise AssetError("retirement requires a full reviewed branch tip SHA")
    if not token:
        raise AssetError("branch retirement requires its separate repository-scoped App token")
    metadata = json.loads(gh("api", "repos/" + repo))
    if branch == metadata["default_branch"]:
        raise AssetError("the default branch cannot be retired")
    for pr in pages(repo, "pulls?state=open&per_page=100"):
        if ((pr["head"].get("repo") or {}).get("full_name") == repo and pr["head"]["ref"] == branch
                or pr["base"]["ref"] == branch):
            raise AssetError("close or merge every PR using the branch before retiring it")
    # A retry after successful deletion may still finish cleanup. Only an actual
    # HTTP 404 establishes absence; network/auth failures are never treated as it.
    ref = "refs/heads/" + branch
    result = subprocess.run(["gh", "api", f"repos/{repo}/git/ref/heads/{quote(branch, safe='')}"],
                            capture_output=True, text=True)
    if result.returncode:
        if not re.search(r"\bHTTP 404\b", result.stderr):
            raise AssetError("cannot establish branch state before retirement")
        summary({"branch": branch, "expected_head": expected, "retirement": "already absent; cleanup retry"})
        return
    actual = json.loads(result.stdout)
    if actual.get("ref") != ref or actual.get("object", {}).get("sha") != expected:
        raise AssetError("branch tip differs from the requested SHA; review it before retiring")
    # Compare-and-delete in Git, rather than a separate API read followed by an
    # unconditional DELETE. A concurrent push makes the lease fail atomically.
    git(root, "-c", "credential.helper=", "-c", "credential.https://github.com.helper=!gh auth git-credential",
        "push", "--no-verify", "--porcelain", "--force-with-lease=" + ref + ":" + expected,
        "https://github.com/" + repo + ".git", ":" + ref,
        env={**os.environ, "GH_TOKEN": token})
    summary({"branch": branch, "expected_head": expected, "retirement": "deleted", "cleanup": "pending"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["request-gc", "request-retire", "retire"])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        if args.command.startswith("request-"):
            request(root, args.command, os.environ)
        else:
            repo = repository(root)
            require_action(repo)
            with operator_lock(root):
                store = B2(credentials(root), operation="delete")
                # Establish a complete retention/storage inventory before deleting
                # a branch. Credentials or scan failures leave that branch intact.
                retained_assets(root, repo)
                store.versions()
                retire(root, repo, os.environ.get("RETIRE_BRANCH", ""), os.environ.get("RETIRE_HEAD_SHA", ""),
                       os.environ.get("PRESS_ASSET_RETIRE_TOKEN", ""))
                count = collect(root, repo, store, apply=True)
                summary({"cleanup": "complete", "deleted_versions": count})
        return 0
    except AssetError as exc:
        print("ERROR:", exc, file=sys.stderr)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print("ERROR: asset maintenance failed; no credential or response details logged", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
