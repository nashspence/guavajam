#!/usr/bin/env python3
"""Validate an image and print its upload details and manuscript URL."""
import argparse
import hashlib
import sys
from pathlib import Path
from asset_policy import AssetError, MIME, load_policy, verify_bytes

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    args = parser.parse_args()
    try:
        policy = load_policy(Path(__file__).resolve().parents[1])
        if policy is None:
            raise AssetError("no approved asset origin configured")
        if args.image.is_symlink():
            raise AssetError("image must not be a symlink")
        raw = args.image.read_bytes()
        extension = args.image.suffix[1:]
        media_type = verify_bytes(raw, extension, MIME.get(extension, ""))
    except (AssetError, OSError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    key = f"sha256/{hashlib.sha256(raw).hexdigest()}.{extension}"
    print("Object:", key)
    print("URL:", f"{policy.origin}/{key}")
    print("Content-Type:", media_type)
    print("Cache-Control: public, max-age=31536000, immutable")
    print("Bytes:", len(raw))
    return 0

if __name__ == "__main__":
    sys.exit(main())
