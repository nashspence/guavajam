#!/usr/bin/env python3
"""Validate an image locally and print its immutable B2 object name and headers."""
import argparse
import hashlib
import sys
from pathlib import Path
from asset_policy import AssetError, MIME, verify_bytes

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    args = parser.parse_args()
    try:
        if args.image.is_symlink():
            raise AssetError("image must not be a symlink")
        raw = args.image.read_bytes()
        extension = args.image.suffix[1:]
        media_type = verify_bytes(raw, extension, MIME.get(extension, ""))
    except (AssetError, OSError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    print("Object: sha256/" + hashlib.sha256(raw).hexdigest() + "." + extension)
    print("Content-Type:", media_type)
    print("Cache-Control: public, max-age=31536000, immutable")
    print("Bytes:", len(raw))
    return 0

if __name__ == "__main__":
    sys.exit(main())
