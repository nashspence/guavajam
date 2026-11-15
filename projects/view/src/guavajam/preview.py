"""Preview an exact local branch privately, without signatures or publication writes.

The optional signed base supplies existing catalog context. The draft is an
explicitly labeled preview, never a fake publication with a fabricated identity.
Canonical source URLs are unchanged; only derived HTML routes staged assets
through a loopback, session-authenticated gateway sharing the production handler.
"""
from html import escape
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile

from guavajam_core.asset_policy import AssetError, load_policy
from guavajam_core.asset_storage import S3, credentials
from guavajam_core.catalog import encoded
from guavajam_core.publication_protocol import discover, resolve
from guavajam_core.qualify_assets import qualify
from guavajam_core.resources import ROOT as RESOURCES
from guavajam_core.source_snapshot import snapshot
from .renderer import render
from .site import build_site


BANNER = '<aside role="note"><strong>PRIVATE DRAFT PREVIEW — NOT A PUBLICATION.</strong> This site is disposable; no public release is authorized.</aside>'


def build_preview(repo, ref, work, output, *, store=None, base_ref=None,
                  fingerprints=(), allowed_signers=None, require_retention=False):
    repo = Path(repo).resolve(strict=True)
    revision = resolve(repo, ref)
    output = Path(output).absolute()
    if output.exists() or output.is_symlink() or output.resolve() != output or output.is_relative_to(repo):
        raise AssetError('preview output must be a new directory outside the source repository without symlink ancestors')
    mappings = qualify(repo, revision, work, store=store, require_retention=require_retention)
    with snapshot(repo, revision) as (root, files):
        policy = load_policy(root)
        origin = policy.origin if policy else None
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.guavajam-preview-', dir=output.parent) as directory:
        stage = Path(directory) / 'site'
        if base_ref:
            catalog, evidence = discover(repo, ref=base_ref, fingerprints=fingerprints, allowed_signers=allowed_signers)
            catalog_path = Path(directory) / 'catalog.json'
            catalog_path.write_bytes(encoded(catalog))
            build_site(repo, catalog_path, stage, fingerprints=fingerprints,
                       allowed_signers=allowed_signers, initial_catalog=True)
        else:
            if fingerprints or allowed_signers:
                raise AssetError('preview signer trust is only for an explicit signed base ref')
            stage.mkdir()
            (stage / 'index.html').write_text('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Draft preview</title></head><body><h1>Draft preview</h1></body></html>\n')
        draft = stage / 'works' / work / 'preview'
        render(repo, revision, work, draft, preview=True, asset_prefix='/_assets')
        source = 'works/' + work + '/manuscript.md'
        from .renderer import git_blob
        (draft / 'source.gfm').write_bytes(git_blob(repo, revision, source))
        link = '<p><a href="/works/' + escape(work) + '/preview/index.html">Review draft ' + escape(work) + ' at ' + revision[:12] + '</a></p>'
        home = stage / 'index.html'
        home.write_text(home.read_text().replace('</body>', link + '</body>'))
        landing = stage / 'works' / work / 'index.html'
        if landing.exists():
            landing.write_text(landing.read_text().replace('</body>', link + '</body>'))
        else:
            landing.parent.mkdir(parents=True, exist_ok=True)
            landing.write_text('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Draft work</title></head><body><h1>Draft work</h1>' + link + '<a href="/">Preview home</a></body></html>\n')
        # Mark every HTML page, including baseline catalog context, as private.
        for page in stage.rglob('*.html'):
            text = page.read_text()
            text = text.replace('<head>', '<head><meta name="robots" content="noindex,nofollow,noarchive"><meta name="referrer" content="no-referrer">', 1)
            text = re.sub(r'(<body\b[^>]*>)', lambda match: match[1] + BANNER, text, count=1)
            if page.is_relative_to(draft):
                text = text.replace('</body>', '<nav><a href="/">Preview home</a></nav></body>')
            page.write_text(text)
        # Generated publication manifests refer to pre-preview bytes. They must
        # not be exposed as verification evidence for the changed presentation.
        for manifest in stage.rglob('VIEW.json'):
            manifest.unlink()
        for name in ('SITE.json',):
            (stage / name).unlink(missing_ok=True)
        inventory = {p.relative_to(stage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in stage.rglob('*') if p.is_file()}
        manifest = {'schema': 'guavajam.preview.v1', 'revision': revision, 'work': work,
                    'origin': origin, 'files': inventory, 'assets': mappings, 'publication': False}
        (stage / 'PREVIEW.json').write_bytes(encoded(manifest))
        stage.chmod(0o700)
        stage.rename(output)
    return manifest


def serve(output, manifest, values, *, port=0):
    node = shutil.which('node')
    if not node:
        raise AssetError('Node is required for the loopback preview server')
    storage = dict(values)
    if manifest['origin']:
        storage['ASSET_HOST'] = manifest['origin'].removeprefix('https://')
    token = secrets.token_hex(32)
    # Credentials never enter rendered files, process arguments, source or logs.
    process = subprocess.Popen([node, str(RESOURCES / 'storage/preview.mjs'), str(output)],
                               stdin=subprocess.PIPE, env={**os.environ, 'GUAVAJAM_PREVIEW_PORT': str(port)})
    try:
        process.stdin.write(json.dumps({'storage': storage, 'token': token}).encode())
        process.stdin.close()
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
    parser.add_argument('--ref', required=True, help='full commit or explicit local refs/heads/<branch>')
    parser.add_argument('--work', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--base-ref', help='optional signed history for the surrounding published site')
    parser.add_argument('--serve', action='store_true', help='serve only on 127.0.0.1 with a private session')
    parser.add_argument('--port', type=int, default=0, help='0 selects an unused loopback port')
    parser.add_argument('--require-retention', action='store_true')
    trust = parser.add_mutually_exclusive_group()
    trust.add_argument('--trusted-gpg-fingerprint', action='append', default=[])
    trust.add_argument('--ssh-allowed-signers', type=Path)
    args = parser.parse_args(argv)
    try:
        if not 0 <= args.port <= 65535:
            raise AssetError('invalid preview port')
        # No credentials are requested for an asset-free draft.
        manifest = build_preview(args.repo, args.ref, args.work, args.output, base_ref=args.base_ref,
                                 fingerprints=args.trusted_gpg_fingerprint, allowed_signers=args.ssh_allowed_signers,
                                 require_retention=args.require_retention)
        if args.serve:
            values = credentials(args.repo) if manifest['assets'] else {}
            return serve(args.output.absolute(), manifest, values, port=args.port)
        print(json.dumps({'revision': manifest['revision'], 'preview': True,
                          'assets': len(manifest['assets']), 'public_release': False}, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('Private preview failed; no publication or public asset state changed.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
