"""Preview exactly one committed draft without issuing or claiming a publication.

An unsigned preview is always labelled as such, even when the selected commit
also happens to be signed. Private assets stay behind the isolated session server.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile

from guavajam.asset_policy import AssetError, load_policy
from guavajam.asset_storage import credentials
from guavajam.catalog import encoded
from guavajam.publication_protocol import resolve
from guavajam.qualify_assets import qualify
from guavajam.resources import ROOT as RESOURCES
from guavajam.source_snapshot import snapshot
from .renderer import render, git_blob


def build_preview(repo, ref, output, *, store=None, require_retention=False):
    repo = Path(repo).resolve(strict=True)
    revision = resolve(repo, ref)
    output = Path(output).absolute()
    if output.exists() or output.is_symlink() or output.resolve() != output or output.is_relative_to(repo):
        raise AssetError('preview output must be a new directory outside the source repository without symlink ancestors')
    mappings = qualify(repo, revision, store=store, require_retention=require_retention)
    with snapshot(repo, revision) as (root, _):
        policy = load_policy(root)
        origin = policy.origin if policy else None
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.guavajam-preview-', dir=output.parent) as directory:
        stage = Path(directory) / 'site'
        render(repo, revision, stage, preview=True)
        (stage / 'source.gfm').write_bytes(git_blob(repo, revision, 'content/manuscript.md'))
        files = {p.relative_to(stage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in stage.rglob('*') if p.is_file()}
        manifest = {'schema': 'guavajam.preview.v1', 'revision': revision,
                    'origin': origin, 'files': files, 'assets': mappings,
                    'publication': False, 'provisional_identity': True}
        (stage / 'PREVIEW.json').write_bytes(encoded(manifest))
        stage.chmod(0o700)
        stage.rename(output)
    return manifest


def start_server(output, manifest, values, *, port=0, token=None, web_origin=None, quiet=False):
    node = shutil.which('node')
    if not node:
        raise AssetError('Node is required for the preview server')
    if web_origin and token is None:
        raise AssetError('hosted preview requires an operator-provisioned session token')
    storage = dict(values)
    if manifest['origin']:
        storage['ASSET_HOST'] = manifest['origin'].removeprefix('https://')
    process = subprocess.Popen([node, str(RESOURCES / 'storage/preview.mjs'), str(output)],
                               stdin=subprocess.PIPE, env={**os.environ, 'GUAVAJAM_PREVIEW_PORT': str(port)},
                               **({'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL} if quiet else {}))
    try:
        process.stdin.write(json.dumps({'storage': storage, 'token': token or secrets.token_hex(32), 'webOrigin': web_origin}).encode())
        process.stdin.close()
        return process
    except BaseException:
        process.terminate(); process.wait()
        raise


def serve(output, manifest, values, *, port=0):
    process = start_server(output, manifest, values, port=port)
    try:
        return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        return process.wait()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait()



def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--ref', required=True, help='full commit or explicit local branch ref')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--require-retention', action='store_true')
    args = parser.parse_args(argv)
    try:
        if not 0 <= args.port <= 65535:
            raise AssetError('invalid preview port')
        manifest = build_preview(args.repo, args.ref, args.output, require_retention=args.require_retention)
        if args.serve:
            values = credentials(args.repo) if manifest['assets'] else {}
            return serve(args.output.absolute(), manifest, values, port=args.port)
        print(json.dumps({'revision': manifest['revision'], 'preview': True,
                          'assets': len(manifest['assets']), 'publication': False}, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('Private preview failed; no publication or public asset state changed.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
