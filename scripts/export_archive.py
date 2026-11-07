#!/usr/bin/env python3
"""Assemble a new offline standby export from an approved commit and saved packages."""
import argparse
import os
from pathlib import Path
import shutil
import sys
import tempfile

from asset_policy import AssetError, MAX_BYTES, MIME, download_bytes, fetch_asset
from audit_assets import edition_page, registered_manifest
from build_edition import COMMIT, git, snapshot
from issuance import check_authorization
from public_record import REGISTRATIONS, WORK_RECORDS, claimed_registrations, regular_record
from verify_edition import verify_edition


def export_archive(checkout, commit, packages, output, *, asset_root=None, fetcher=fetch_asset):
    checkout = Path(git(checkout, "rev-parse", "--show-toplevel")).resolve()
    if not COMMIT.fullmatch(commit):
        raise AssetError("expected the independently retained full commit SHA")
    if git(checkout, "rev-parse", "--is-shallow-repository") == "true":
        raise AssetError("standby export requires a full repository, not a shallow clone")
    output = output.parent.resolve(strict=True) / output.name
    if output.exists() or output.is_symlink() or output.is_relative_to(checkout):
        raise AssetError("output must be a new directory outside the Press checkout")
    packages = Path(os.path.abspath(packages))
    if asset_root is not None:
        asset_root = Path(os.path.abspath(asset_root))
        if asset_root.resolve() != asset_root or not asset_root.is_dir():
            raise AssetError('ASSET_ROOT requires a saved asset directory without symlinks')
    def asset_fetch(policy, url):
        if asset_root is None:
            return fetcher(policy, url)
        digest, extension = policy.parse(url)
        path = asset_root/'sha256'/(digest+'.'+extension)
        regular_record(path)
        with path.open('rb') as stream:
            return stream.read(MAX_BYTES+1), MIME.get(extension,'application/octet-stream')
    with tempfile.TemporaryDirectory(prefix=".press-export-", dir=output.parent) as temp:
        staging = Path(temp)
        archive = staging / "export"
        public = archive / "public"
        public.mkdir(parents=True)
        # Create the named bundle ref in isolation; leave the operator's refs untouched.
        repo = staging / "inventory.git"
        git(staging, "init", "--bare", "--initial-branch=archive", str(repo))
        git(repo, "fetch", "--no-tags", str(checkout), commit + ":refs/heads/archive")
        bundle = archive / "archive.bundle"
        git(repo, "bundle", "create", str(bundle), "HEAD", "refs/heads/archive")
        # Restore into an empty repository so other local objects cannot mask an incomplete bundle.
        restored = staging / "restored.git"
        git(staging, "clone", "--bare", str(bundle), str(restored))
        if git(restored, "rev-parse", "HEAD") != commit:
            raise AssetError("restored archive bundle differs from the approved commit")
        git(restored, "fsck", "--full")
        inventory = staging / "inventory"
        paths = snapshot(restored, commit, inventory)

        editions = 0
        registrations = (path for path in paths if path.is_relative_to(REGISTRATIONS))
        for slug, version, receipt in claimed_registrations(inventory, registrations):
            if "published_at" not in receipt:
                continue
            tag = f"works/{slug}/{version}"
            registered_manifest(inventory, tag, receipt)
            source = packages / tag
            if source.resolve() != source or not source.is_dir():
                raise AssetError(f"{source}: expected a saved edition directory without symlinks")
            dest = public / tag
            dest.mkdir(parents=True)
            for path in source.iterdir():
                regular_record(path)
                shutil.copyfile(path, dest / path.name)
            manifest = verify_edition(dest, tag=tag)
            check_authorization(receipt, dest, manifest)
            for url, raw, _ in download_bytes(manifest, fetcher=asset_fetch):
                key = url.removeprefix(manifest['asset_origin']+'/')
                asset = public/key
                asset.parent.mkdir(parents=True, exist_ok=True)
                if asset.exists() and asset.read_bytes() != raw:
                    raise AssetError('conflicting saved external asset')
                asset.write_bytes(raw)
            shutil.copyfile(dest / manifest["canonical_artifact"], dest / "read.html")
            (dest / "index.html").write_bytes(edition_page(inventory, tag))
            work_page = dest.parent / "index.html"
            if not work_page.exists():
                shutil.copyfile(inventory / WORK_RECORDS / slug / "index.html", work_page)
            editions += 1
        if not editions:
            raise AssetError("trusted inventory contains no issued editions")
        (archive / "ARCHIVE_COMMIT").write_text(commit + "\n", encoding="ascii")
        archive.rename(output)
    return editions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--package-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument('--asset-root', type=Path, help='saved sha256 objects; otherwise fetch external downloads')
    args = parser.parse_args()
    try:
        count = export_archive(Path.cwd(), args.commit, args.package_root, args.output, asset_root=args.asset_root)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Exported {count} issued editions at {args.commit} to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
