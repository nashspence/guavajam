#!/usr/bin/env python3
"""Verify referenced asset bytes on every push/PR; uses no storage credentials."""
from collections import Counter
import hashlib
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

from asset_policy import (AssetError, MAX_ASSETS, MAX_TOTAL_BYTES, base64_size, check_html_size, fetch_asset,
                          load_policy, verify_bytes)
from validate_sources import git_sources, local_asset_size, parse_document, walk


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
        counts = Counter(node["c"][2][0] for node in walk(ast["blocks"]) if node.get("t") == "Image")
        if not manuscript:
            counts = {url: count for url, count in counts.items() if policy and url.startswith(policy.origin + "/")}
        if len(counts) > MAX_ASSETS:
            raise AssetError(f"{path} exceeds the distinct image limit")
        total = embedded = 0
        for target, count in counts.items():
            if ":" in target or target.startswith("//"):
                if policy is None:
                    raise AssetError("no approved asset origin configured")
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
            total += size
            if total > MAX_TOTAL_BYTES:
                raise AssetError(f"{path} exceeds the total image byte limit")
            if manuscript:
                embedded += count * base64_size(size)
                check_html_size(embedded)
    return len(remote_sizes)


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
