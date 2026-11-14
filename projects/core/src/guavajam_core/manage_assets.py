#!/usr/bin/env python3
"""Upload original images or conservatively collect abandoned asset objects."""
from __future__ import annotations

from .repository import repository_root
import argparse
import fcntl
import html
import json
import os
import re
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote

from .asset_policy import ASSET_PATH, AssetError, fetch_asset, load_policy
from .asset_storage import S3, credentials
from .object_store import GRACE_DAYS, LEASE_PREFIX, PREFIX
from .prepare_asset import prepare_image
from .github import gh

SHA = re.compile(r"[0-9a-f]{40}\Z")
FINGERPRINT = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")
MAX_SCAN_BYTES = 256 * 1024 * 1024


def git(root, *args, data=None, env=None):
    result = subprocess.run(["git", "--no-replace-objects", *args], cwd=root,
                            input=data, capture_output=True, env=env)
    if result.returncode:
        raise AssetError("cannot establish complete Git asset retention roots")
    return result.stdout


@contextmanager
def operator_lock(root):
    path = os.fsdecode(git(root, "rev-parse", "--git-path", "press-assets.lock").strip())
    with (root / path).open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AssetError("another asset operation is running in this checkout") from None
        yield


def upload(root, image, store, *, fetcher=fetch_asset, index=None):
    policy = load_policy(root)
    if policy is None:
        raise AssetError("no approved asset origin configured")
    raw, key, media_type = prepare_image(image)
    # A server-timestamped lease protects both a new image and reuse of an old
    # orphan before its manuscript is pushed. No author or local path is stored.
    lease = json.dumps({"schema": "press.asset-lease.v1", "key": key}, sort_keys=True).encode()
    store.upload(LEASE_PREFIX + key[len(PREFIX):], lease, "application/json", cache_control="no-store")
    existing = store.current(key)
    if existing is not None:
        if existing.delete_marker:
            raise AssetError("existing asset is hidden or unfinished; inspect storage before restoring it")
        state = "Already stored"
    else:
        store.upload(key, raw, media_type, create_only=True)
        state = "Uploaded"
    # Check storage directly as well as the gateway: a valid cached copy must not mask
    # corrupt or missing bytes at the origin. Never overwrite a conflicting key.
    store.verify(key, raw, media_type)
    url = policy.origin + "/" + key
    if index is not None:
        from .asset_versions import pin_key
        pin_key(store,index,key)
    received, received_type = fetcher(policy, url)
    if received != raw or received_type != media_type:
        raise AssetError("public asset read-back differs from the original image")
    return state, url


def repository(root):
    remote = os.fsdecode(git(root, "remote", "get-url", "origin")).strip()
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?", remote)
    if not match:
        raise AssetError("asset cleanup requires a canonical GitHub origin remote")
    return match[1]


def pages(repo, endpoint):
    raw = gh("api", "--paginate", f"repos/{repo}/{endpoint}").lstrip()
    if not raw:
        raise AssetError("empty GitHub asset retention inventory")
    decoder, records = json.JSONDecoder(), []
    while raw:
        page, end = decoder.raw_decode(raw)
        if not isinstance(page, list):
            raise AssetError("incomplete GitHub asset retention inventory")
        records.extend(page)
        raw = raw[end:].lstrip()
    return records


def remote_roots(repo):
    roots = {}
    try:
        for branch in pages(repo, "branches?per_page=100"):
            name = "branch:" + branch["name"]
            if name in roots:
                raise ValueError()
            roots[name] = branch["commit"]["sha"]
        for pr in pages(repo, "pulls?state=open&per_page=100"):
            name = "pr:" + str(pr["number"])
            if pr["state"] != "open" or name in roots:
                raise ValueError()
            roots[name] = pr["head"]["sha"]
        if ("branch:main" not in roots or len(roots) > 1000
                or any(not isinstance(sha, str) or not SHA.fullmatch(sha) for sha in roots.values())):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise AssetError("incomplete GitHub branch or pull-request inventory") from None
    return roots


def fingerprints(raw):
    # Deliberately retain every digest token, even in prose/code or under a
    # historical origin. Normalize JSON escapes and Markdown URL entities first.
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m[1], 16)), text)
    text = html.unescape(unquote(text))
    return set(FINGERPRINT.findall(text))


def scan_blobs(root, objects):
    if not objects:
        return set()
    if len(objects) > 100_000:
        raise AssetError("Git retention inventory exceeds the bounded scan limit")
    supplied = ("\n".join(sorted(objects)) + "\n").encode()
    info = git(root, "cat-file", "--batch-check", data=supplied).splitlines()
    blobs, total = [], 0
    for line in info:
        fields = line.split()
        if len(fields) != 3 or fields[1] not in {b"blob", b"tree", b"commit", b"tag"}:
            raise AssetError("missing Git object in asset retention inventory")
        if fields[1] == b"blob":
            size = int(fields[2])
            total += size
            blobs.append(fields[0].decode())
    if total > MAX_SCAN_BYTES:
        raise AssetError("Git retention inventory exceeds the bounded byte limit")
    if not blobs:
        return set()
    batch = git(root, "cat-file", "--batch", data=("\n".join(blobs) + "\n").encode())
    retained, pos = set(), 0
    for sha in blobs:
        end = batch.index(b"\n", pos)
        header = batch[pos:end].split()
        if len(header) != 3 or header[:2] != [sha.encode(), b"blob"]:
            raise AssetError("incomplete Git blob scan")
        size = int(header[2])
        raw = batch[end + 1:end + 1 + size]
        if len(raw) != size or batch[end + 1 + size:end + 2 + size] != b"\n":
            raise AssetError("truncated Git blob scan")
        retained.update(fingerprints(raw))
        pos = end + 2 + size
    if pos != len(batch):
        raise AssetError("unexpected Git blob scan data")
    return retained


def retained_assets(root, repo):
    if git(root, "rev-parse", "--is-shallow-repository").strip() != b"false":
        raise AssetError("asset cleanup requires complete main history, not a shallow clone")
    grafts = Path(os.fsdecode(git(root, "rev-parse", "--git-path", "info/grafts").strip()))
    if (root / grafts).exists() or git(root, "replace", "--list").strip():
        raise AssetError("asset cleanup requires unmodified Git history without grafts or replacements")
    roots = remote_roots(repo)
    commits = sorted(set(roots.values()))
    git(root, "fetch", "--no-tags", "--no-write-fetch-head", "origin", *commits)
    main = roots["branch:main"]
    objects = set(git(root, "rev-list", "--objects", "--no-object-names", main).decode().splitlines())
    local = git(root, "for-each-ref", "--format=%(objectname)", "refs/heads").decode().splitlines()
    for commit in set(commits + local):
        for row in git(root, "ls-tree", "-r", "-z", commit).split(b"\0"):
            if not row:
                continue
            fields = row.split(b"\t", 1)[0].split()
            if len(fields) != 3 or fields[1] != b"blob":
                raise AssetError("asset retention cannot scan a submodule or incomplete tree")
            objects.add(fields[2].decode())
    # Include the index, including conflict stages, and uncommitted/untracked
    # local author work. Ignore ignored files such as credentials and build output.
    for row in git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if row:
            objects.add(row.split(b"\t", 1)[0].split()[1].decode())
    retained = scan_blobs(root, objects)
    local_bytes = 0
    paths = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    for name in set(paths.split(b"\0")) - {b""}:
        path = root / os.fsdecode(name)
        if path.is_symlink():
            raise AssetError("asset cleanup refuses symlinks in the local source inventory")
        if path.exists():
            if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
                raise AssetError("asset cleanup requires regular local source files")
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise AssetError("asset cleanup requires regular local source files")
                raw = stream.read(MAX_SCAN_BYTES - local_bytes + 1)
                local_bytes += len(raw)
                if local_bytes > MAX_SCAN_BYTES:
                    raise AssetError("local retention inventory exceeds the bounded byte limit")
                retained.update(fingerprints(raw))
    if remote_roots(repo) != roots:
        raise AssetError("GitHub retention roots changed during the scan; retry cleanup")
    return retained


def asset_key(name):
    from .gateway_index import PIN_PREFIX
    if name.startswith(PIN_PREFIX) and name.endswith('.json'):
        candidate=PREFIX+name[len(PIN_PREFIX):-5]
    else:
        candidate = PREFIX + name[len(LEASE_PREFIX):] if name.startswith(LEASE_PREFIX) else name
    return candidate if ASSET_PATH.fullmatch("/" + candidate) else None


def groups(objects):
    result = {}
    for item in objects:
        key = asset_key(item.key)
        if key is not None:
            result.setdefault(key, []).append(item)
    return result


def candidates(objects, retained, now):
    cutoff = (now - GRACE_DAYS * 86400) * 1000
    result = {}
    for key, items in groups(objects).items():
        digest = ASSET_PATH.fullmatch("/" + key)[1]
        if (digest not in retained and all(item.modified_ms < cutoff for item in items)
                and all(not item.legal_hold and (item.retain_until or 0) <= now * 1000 for item in items)):
            result[key] = items
    return result


def collection_order(item):
    return item.key, item.version_id or '', item.etag or ''


def collect(root, repo, store, *, apply=False, now=None, scan=retained_assets):
    now = time.time() if now is None else now
    retained = scan(root, repo)
    planned = candidates(store.inventory(), retained, now)
    print(f"Retained digest tokens: {len(retained)}; grace: {GRACE_DAYS} days")
    print(f"Unreferenced candidates: {len(planned)} assets; {sum(map(len, planned.values()))} stored objects (including leases and pins)")
    deleted = 0
    for key, items in sorted(planned.items()):
        print("Candidate:", key)
        for item in sorted(items, key=collection_order):
            print(f"  {item.version_id or 'unversioned'} {item.byte_count} bytes; delete marker: {item.delete_marker}")
        if not apply:
            continue
        # No persisted plan authorizes deletion. Writers and reference changes
        # must remain paused for the entire sweep, including unversioned buckets.
        current_retained = scan(root, repo)
        current = store.inventory()
        refreshed = candidates(current, current_retained, now).get(key)
        if refreshed is None or sorted(refreshed, key=collection_order) != sorted(items, key=collection_order):
            raise AssetError("asset references, objects or leases changed; retry cleanup")
        # Delete hidden content before its delete marker, avoiding resurrection.
        ordered = sorted(items, key=lambda item: (item.delete_marker, item.modified_ms, collection_order(item)))
        for item in ordered:
            store.delete(item)
            deleted += 1
    print(f"Deleted objects: {deleted}" if apply else "Preview only; no objects deleted.")
    return deleted


def require_action(repo):
    if (os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("GITHUB_REF") != "refs/heads/main"
            or os.environ.get("GITHUB_REPOSITORY") != repo
            or os.environ.get("GITHUB_WORKFLOW_REF") != repo + "/.github/workflows/maintain-assets.yml@refs/heads/main"
            or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"):
        raise AssetError("deletion requires a manual dispatch of the main-branch asset maintenance Action")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    push = commands.add_parser("upload")
    push.add_argument("image", type=Path)
    gc = commands.add_parser("gc")
    gc.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = repository_root()
    try:
        with operator_lock(root):
            if args.command == "upload":
                # Validate before requesting a credential or touching storage.
                typed=args.image.suffix in {'.mp3','.mp4','.vtt'}
                if not typed:prepare_image(args.image)
                from .gateway_index import Index
                store=S3(credentials(root),operation='upload')
                index=Index(store)
                if typed:
                    from .ingest_asset import upload_media
                    state,url=upload_media(root,args.image,store,index)
                else:state, url = upload(root, args.image, store,index=index)
                print(f"{state} and verified: {url}")
                print(f'[Download media]({url})' if typed else f'![Describe the image]({url})')
            else:
                repo = repository(root)
                if args.apply:
                    require_action(repo)
                store = S3(credentials(root), operation="delete" if args.apply else "preview")
                collect(root, repo, store, apply=args.apply)
        return 0
    except AssetError as exc:
        print("ERROR:", exc, file=sys.stderr)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print("ERROR: asset operation failed; no credential or response details logged", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
