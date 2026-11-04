#!/usr/bin/env python3
"""Verify referenced asset bytes on every push/PR; uses no storage credentials."""
import hashlib
import sys
from pathlib import Path

from asset_policy import (AssetError, MAX_ASSETS, MAX_TOTAL_BYTES, fetch_asset,
                          load_policy, verify_bytes)
from validate_sources import git_sources, parse_document, walk


def verify_source_assets(root, *, fetcher=fetch_asset):
    policy = load_policy(root)
    urls = set()
    for path in git_sources(root):
        if path.suffix != ".md":
            continue
        ast, errors = parse_document(root / path, path)
        if errors:
            raise AssetError("\n".join(errors))
        manuscript = len(path.parts) == 3 and path.parts[0] == "works" and path.name == "manuscript.md"
        for node in walk(ast["blocks"]):
            if node.get("t") != "Image":
                continue
            target = node["c"][2][0]
            remote = ":" in target or target.startswith("//")
            if remote and (manuscript or (policy and target.startswith(policy.origin + "/"))):
                if policy is None:
                    raise AssetError("no approved asset origin configured")
                policy.parse(target)
                urls.add(target)
    if len(urls) > MAX_ASSETS:
        raise AssetError("source snapshot exceeds 128 distinct remote images")
    total = 0
    for url in sorted(urls):
        digest, extension = policy.parse(url)
        raw, media_type = fetcher(policy, url)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise AssetError("asset SHA-256 does not match URL")
        verify_bytes(raw, extension, media_type)
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise AssetError("source snapshot exceeds 100 MiB downloaded image bytes")
    return len(urls)


def main():
    try:
        count = verify_source_assets(Path(__file__).resolve().parents[1])
    except (AssetError, OSError, ValueError, TypeError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    print(f"Verified manuscript assets: {count} content-addressed HTTPS objects")
    return 0


if __name__ == "__main__":
    sys.exit(main())
