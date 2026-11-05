"""Confirm absence of an edition after a permanent stop has drained the publication job."""
import argparse
import os
from pathlib import Path
import sys

from asset_policy import AssetError
from build_edition import COMMIT, git
from identifier_schema import edition_registration_path, strict_json
from issuance import read_registration
from publish_edition import api, release_records, tag_commit


def require_absent(repo, tag, decision="abandonment"):
    if tag_commit(repo, tag) is not None or any(release["tag_name"] == tag for release in release_records(repo)):
        raise AssetError(f"{tag}: {decision} requires absence of the tag and all draft or public releases")


def confirm_stop(root, repo, tag):
    record = read_registration(root, edition_registration_path(tag))
    if (record.get("issuing") is not True or not record.get("stop_reason")
            or "published_at" in record or "abandoned_run_id" in record):
        raise AssetError(f"{tag}: confirmation requires a stopped, unpublished claim")
    require_absent(repo, tag)


def verify_confirmation(root, repo, path, base, stopped, run_id):
    """Only a successful serialized check of this exact stop on main can retire it."""
    tag = f"works/{stopped['work']}/{stopped['version']}"
    run = api(repo, f"actions/runs/{run_id}")
    sha = run["head_sha"]
    if (run["path"] != ".github/workflows/confirm-abandonment.yml" or run["event"] != "workflow_dispatch"
            or run["head_repository"]["full_name"] != repo or run["head_branch"] != "main"
            or run["status"] != "completed" or run["conclusion"] != "success" or not COMMIT.fullmatch(sha)):
        raise AssetError(f"{tag}: expected a successful abandonment confirmation on main")
    git(root, "merge-base", "--is-ancestor", sha, base)
    previous, errors = strict_json(git(root, "show", f"{sha}:{path.as_posix()}", binary=True), path)
    if errors or previous != stopped:
        raise AssetError(f"{tag}: confirmation must postdate the exact committed stop")
    jobs = api(repo, f"actions/runs/{run_id}/attempts/{run['run_attempt']}/jobs?per_page=100")
    if jobs["total_count"] != 1 or len(jobs["jobs"]) != 1:
        raise AssetError(f"{tag}: expected one confirmation job")
    job = jobs["jobs"][0]
    if (job["name"] != f"Confirm {tag}" or job["conclusion"] != "success"
            or not any(step["name"] == "Verify release and tag absence" and step["conclusion"] == "success"
                       for step in job["steps"])):
        raise AssetError(f"{tag}: confirmation job must have verified this edition")
    require_absent(repo, tag)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    args = parser.parse_args()
    try:
        confirm_stop(Path(__file__).resolve().parents[1], os.environ["GITHUB_REPOSITORY"], args.tag)
    except (OSError, ValueError, KeyError) as exc:
        sys.exit(f"Abandonment confirmation failed: {exc}")
