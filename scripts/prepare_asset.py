#!/usr/bin/env python3
"""Validate an image and print its upload details and manuscript URL."""
import argparse
import hashlib
import os
import stat
import sys
from pathlib import Path
from asset_policy import AssetError, MAX_BYTES, MIME, load_policy, verify_bytes


def prepare_image(image):
    """Read bounded original bytes once, without following a final symlink."""
    descriptor = os.open(image, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise AssetError("image must be a regular file, not a symlink or device")
        raw = stream.read(MAX_BYTES + 1)
    extension = image.suffix[1:]
    media_type = verify_bytes(raw, extension, MIME.get(extension, ""))
    key = f"sha256/{hashlib.sha256(raw).hexdigest()}.{extension}"
    return raw, key, media_type

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    args = parser.parse_args()
    try:
        policy = load_policy(Path(__file__).resolve().parents[1])
        if policy is None:
            raise AssetError("no approved asset origin configured")
        raw, key, media_type = prepare_image(args.image)
    except (AssetError, OSError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 1
    print("Object:", key)
    print("URL:", f"{policy.origin}/{key}")
    print("Content-Type:", media_type)
    print("Cache-Control: public, max-age=31536000, immutable")
    print("Bytes:", len(raw))
    return 0

if __name__ == "__main__":
    sys.exit(main())
