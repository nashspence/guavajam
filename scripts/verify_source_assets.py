#!/usr/bin/env python3
"""Verify referenced assets, with a CI exception for editorial metadata changes."""
from collections import Counter
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

from asset_policy import (AssetError, MAX_ASSETS, MAX_TOTAL_BYTES, base64_size, check_html_size, fetch_asset,
                          load_policy, verify_bytes)
from validate_sources import git_sources, local_asset_size, parse_document, walk
from identifier_schema import SLUG, VERSION, valid_utc


def notice_base(root, event_base):
    """Compare a branch push with trusted main, including its first push."""
    ref = os.environ.get("GITHUB_REF", "")
    if (os.environ.get("GITHUB_EVENT_NAME") == "push"
            and ref.startswith("refs/heads/") and ref != "refs/heads/main"):
        result = subprocess.run(["git", "merge-base", "HEAD", "refs/remotes/origin/main"],
                                cwd=root, capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else ""
    return event_base


def editorial_only_change(root, base):
    """Skip online reads only for editorial changes verified by the independent history and record checks."""
    if not re.fullmatch(r"[0-9a-f]{40}", base) or base == "0" * 40:
        return False
    if subprocess.run(["git", "merge-base", "--is-ancestor", base, "HEAD"], cwd=root, capture_output=True).returncode:
        return False
    diff = subprocess.check_output(["git", "diff", "--name-only", "--no-renames", "-z", base, "HEAD", "--"], cwd=root)
    notice = rf"notices/{SLUG.pattern}/{VERSION.pattern}/[0-9]{{4}}\.md"
    page = rf"records/(?:editions/{SLUG.pattern}/{VERSION.pattern}|works/{SLUG.pattern})/index\.html"
    edition = rf"registrations/editions/({SLUG.pattern})/({VERSION.pattern})\.json"
    work = rf"registrations/works/{SLUG.pattern}\.json"
    paths = {os.fsdecode(path) for path in diff.split(b"\0") if path}
    allowed = {path for path in paths if re.fullmatch(notice, path) or re.fullmatch(work, path)}
    for path in paths:
        match = re.fullmatch(edition, path)
        if not match:
            continue
        # Reservation and initial claim changes still run the full online check.
        old = subprocess.run(["git", "show", f"{base}:{path}"], cwd=root, capture_output=True)
        new = subprocess.run(["git", "show", f"HEAD:{path}"], cwd=root, capture_output=True)
        if old.returncode or new.returncode:
            return False
        try:
            before, after = json.loads(old.stdout), json.loads(new.stdout)
        except (ValueError, UnicodeError):
            return False
        if not (isinstance(before, dict) and isinstance(after, dict)
                and before.get("issuing") is True and after.get("issuing") is True):
            return False
        receipted = valid_utc(after.get("published_at"))
        if receipted and "published_at" not in before:
            allowed.add(f"records/editions/{match[1]}/{match[2]}/provenance.json")
        if not (receipted or after.get("stop_reason") or after.get("abandoned_run_id")):
            return False
        allowed.add(path)
    return bool(allowed) and all(path in allowed or re.fullmatch(page, path) for path in paths)


def verify_source_assets(root, *, fetcher=fetch_asset):
    root = root.resolve()
    policy = load_policy(root)
    tracked = set(git_sources(root))
    remote_sizes = {}
    for path in sorted(tracked):
        if path.suffix != ".md":
            continue
        ast, errors = parse_document(root / path, path)
        if errors:
            raise AssetError("\n".join(errors))
        manuscript = len(path.parts) == 3 and path.parts[0] == "works" and path.name == "manuscript.md"
        if manuscript:
            from supporting_records import append_supporting_records
            append_supporting_records(ast, root, path, tracked)
        images = Counter(node['c'][2][0] for node in walk(ast['blocks']) if node.get('t') == 'Image')
        downloads = {node['c'][2][0] for node in walk(ast['blocks']) if node.get('t') == 'Link'
                     and policy and policy.manages(node['c'][2][0])}
        if not manuscript:
            images = {url:count for url,count in images.items() if policy and policy.manages(url)}
        if len(images) + len(downloads) > MAX_ASSETS:
            raise AssetError(f"{path} exceeds the distinct asset limit")
        total = embedded = 0
        for target in images.keys() | downloads:
            if ":" in target or target.startswith("//"):
                if policy is None:
                    raise AssetError("no approved asset origin configured")
                (policy.parse_image if target in images else policy.parse)(target)
                if target not in remote_sizes:
                    digest, extension = policy.parse(target)
                    raw, media_type = fetcher(policy, target)
                    if hashlib.sha256(raw).hexdigest() != digest:
                        raise AssetError("asset SHA-256 does not match URL")
                    verify_bytes(raw, extension, media_type)
                    remote_sizes[target] = len(raw)
                size = remote_sizes[target]
            else:
                parsed = urlsplit(target)
                local = (root / path.parent / unquote(parsed.path)).resolve()
                if (parsed.query or parsed.fragment or not local.is_relative_to(root / path.parent / "assets")
                        or local.relative_to(root) not in tracked):
                    raise AssetError(f"{path}: invalid local edition image: {target}")
                size = local_asset_size(local)
            total += size * ((1 if target in images else 0) + (1 if target in downloads else 0))
            if total > MAX_TOTAL_BYTES:
                raise AssetError(f"{path} exceeds the total image byte limit")
            if manuscript and target in images:
                embedded += images[target] * base64_size(size)
                check_html_size(embedded)
    return len(remote_sizes)


def main():
    try:
        root = Path(__file__).resolve().parents[1]
        if editorial_only_change(root, notice_base(root, os.environ.get("PRESS_NOTICE_BASE_SHA", ""))):
            print("Only editorial metadata and record files changed; no manuscript image inputs changed.")
            return 0
        count = verify_source_assets(root)
    except (AssetError, OSError, ValueError, TypeError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    print(f"Verified manuscript assets: {count} content-addressed HTTPS objects")
    return 0


if __name__ == "__main__":
    sys.exit(main())
