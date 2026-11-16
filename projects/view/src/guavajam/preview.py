"""Assemble the whole proposed GuavaJam site from an exact local Git branch.

Signed candidates use the unmodified production builder and canonical bytes. Unsigned
candidates are provisional catalog overlays rendered by the same site builder;
only the selected proposed edition bypasses signature verification. Both are
served privately with staged-asset access; neither publishes, signs or pushes.
"""
import argparse
import copy
import re
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile

from guavajam_core.asset_policy import AssetError, load_policy
from guavajam_core.asset_storage import S3, credentials
from guavajam_core.catalog import encoded, validate, compare, day
from guavajam_core.publication_protocol import discover, resolve, ancestor, declared_history, run, blob, policy
from guavajam_core.qualify_assets import qualify
from guavajam_core.resources import ROOT as RESOURCES
from guavajam_core.source_snapshot import snapshot
from .renderer import git_blob
from .site import build_site


def proposed_catalog(catalog, revision, work, published, label, manuscript, *, recommendation_locked=False):
    """Add one non-authoritative edition to the reviewed signed catalog.

    The current signed catalog supplies all existing works and editorial settings.
    Never infer an editorial date, mutate existing DOI bindings or imply that a
    draft SHA is a signed publication identity.
    """
    from guavajam_core.validate_sources import validate_front_matter
    from guavajam_core.identifiers import SLUG
    if not SLUG.fullmatch(work):
        raise AssetError('invalid preview work slug')
    date = day(published, 'proposed publication date')
    from pathlib import Path
    metadata, _, _, errors = validate_front_matter(Path('works')/work/'manuscript.md', manuscript.decode('utf-8', 'strict'))
    if errors or metadata is None or metadata.get('slug') != work:
        raise AssetError('invalid proposed manuscript metadata')
    item = {'revision': revision, 'published': date, 'status': 'available'}
    if label is not None:
        from guavajam_core.catalog import text
        item['label'] = text(label, 'proposed edition label')
    matches = [item for item in catalog['works'] if item['slug'] == work]
    if len(matches) > 1 or (matches and any(e['revision'] == revision for e in matches[0]['editions'])):
        raise AssetError('proposed edition conflicts with a discovered publication')
    if matches:
        existing = matches[0]
        original_doi = existing.get('concept_doi')
        proposed_doi = metadata.get('identifiers', {}).get('concept_doi')
        if proposed_doi and proposed_doi != original_doi:
            raise AssetError('changing an issued work DOI requires reviewed site administration')
        previous_latest = max((e for e in existing['editions'] if e['status'] == 'available'),
                              key=lambda e: e['published'], default=None)
        existing['editions'].append(item)
        # Only advance an implicit/default recommendation. An explicitly
        # disabled or older recommendation remains under signed site control.
        existing['editions'].sort(key=lambda x: x['published'])
        if (not recommendation_locked and previous_latest and existing.get('recommended_revision') == previous_latest['revision']
                and date >= previous_latest['published']):
            existing['recommended_revision'] = revision
    else:
        candidate = {'slug': work, 'metadata_revision': revision, 'recommended_revision': revision,
                     'editions': [item]}
        if metadata.get('identifiers', {}).get('concept_doi'):
            candidate['concept_doi'] = metadata['identifiers']['concept_doi']
        catalog['works'].append(candidate)
        catalog['works'].sort(key=lambda x: x['slug'])
    validate(catalog)
    return catalog


def preview_refs(repo):
    """Only bounded local publication branches; never fetch or execute source."""
    rows = run(repo, 'for-each-ref', '--format=%(refname) %(objectname)',
               'refs/heads/', 'refs/remotes/', limit=32768).decode('ascii').splitlines()
    if len(rows) > 128:
        raise AssetError('too many preview branches')
    return dict(row.split(' ', 1) for row in rows)


def provisional_settings(stage):
    """Mark every derived HTML surface; exact source downloads stay untouched."""
    notice = ('<aside role="note" aria-label="Provisional repository" '
              'style="background:#222;color:#fff;padding:1rem">'
              'UNSIGNED REPOSITORY PREVIEW — publisher settings are provisional; '
              'no publication has been issued.</aside>')
    for path in stage.rglob('*.html'):
        html = path.read_text('utf-8')
        html = re.sub(r'(<body\b[^>]*>)', lambda match: match[1] + notice, html, count=1)
        path.write_text(html, encoding='utf-8')


def build_preview(repo, ref, work, output, *, store=None, base_ref=None,
                  fingerprints=(), allowed_signers=None, require_retention=False,
                  published=None, label=None, baseline=None):
    """Build the entire hypothetical site, atomically and without public effects.

    A declaration-free repository needs no signer policy or protected branch.
    Declared history always uses independent verification, never a fallback.
    A saved verified baseline prevents session downgrade and lost publications.
    """
    repo = Path(repo).resolve(strict=True)
    revision, declarations = declared_history(repo, ref)
    if fingerprints and allowed_signers:
        raise AssetError('choose exactly one publisher signing policy')
    output = Path(output).absolute()
    if output.exists() or output.is_symlink() or output.resolve() != output or output.is_relative_to(repo):
        raise AssetError('preview output must be a new directory outside the source repository without symlink ancestors')
    if baseline is not None:
        if set(baseline) != {'schema', 'checkpoint', 'catalog'} or baseline['schema'] != 'guavajam.preview-baseline.v1':
            raise AssetError('invalid verified preview baseline')
        validate(baseline['catalog'])
        ancestor(repo, baseline['checkpoint'], revision)
    provisional_repository = not declarations
    if provisional_repository:
        _, known = declared_history(repo, revision, additional_refs=tuple(preview_refs(repo)))
        if known or baseline is not None:
            raise AssetError('preview branch omits known repository declarations')
        base = resolve(repo, base_ref) if base_ref else revision
        ancestor(repo, base, revision)
        settings, catalog = policy(blob(repo, revision, 'guavajam.json'))
        if settings.get('works') or settings.get('editions'):
            raise AssetError('provisional repository cannot claim issued works or editions')
        catalog.setdefault('base_url', 'https://preview.invalid')
        previous = copy.deepcopy(catalog)
        checkpoint, verified_catalog = None, None
    else:
        if not (fingerprints or allowed_signers):
            raise AssetError('declared history requires independently trusted publisher signing policy')
        refs = preview_refs(repo)
        default_base = next((name for name in ('refs/heads/main', 'refs/remotes/origin/main') if name in refs), revision)
        base = resolve(repo, base_ref or default_base)
        ancestor(repo, base, revision)
        previous, _ = discover(repo, ref=base, fingerprints=fingerprints, allowed_signers=allowed_signers)
        catalog, evidence = discover(repo, ref=revision, fingerprints=fingerprints, allowed_signers=allowed_signers,
                                    previous=previous)
        if baseline is not None:
            compare(baseline['catalog'], catalog)
        checkpoint, verified_catalog = evidence['repository_checkpoint'], copy.deepcopy(catalog)
    # The exact candidate must either declare the selected signed edition, or
    # be a committed draft to be represented provisionally in the whole catalog.
    from guavajam_core.publication_protocol import declaration, commit
    declared = declaration(commit(repo, revision)[1])
    signed = declared is not None and declared.get('kind') == 'publication'
    if declared is not None and not signed:
        raise AssetError('select a content draft or a signed publication, not a repository checkpoint')
    if signed and declared['manuscript'] != f'works/{work}/manuscript.md':
        raise AssetError('selected signed publication declares a different work')
    if signed and (published is not None and published != declared['published'] or
                   label is not None and label != declared.get('label')):
        raise AssetError('preview facts differ from the signed publication declaration')
    mappings = qualify(repo, revision, work, store=store, require_retention=require_retention)
    with snapshot(repo, revision) as (root, _):
        asset_policy = load_policy(root)
        origin = asset_policy.origin if asset_policy else None
    if not signed:
        if published is None:
            raise AssetError('unsigned full-site preview requires PUBLISHED=YYYY-MM-DD')
        settings = json.loads(blob(repo, revision, 'guavajam.json'))
        locked = 'recommended_revision' in settings.get('works', {}).get(work, {})
        catalog = proposed_catalog(catalog, revision, work, published, label,
                                   git_blob(repo, revision, f'works/{work}/manuscript.md'),
                                   recommendation_locked=locked)
    compare(previous, catalog)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.guavajam-preview-', dir=output.parent) as directory:
        stage = Path(directory) / 'site'
        catalog_path = Path(directory) / 'catalog.json'
        catalog_path.write_bytes(encoded(catalog))
        prior_path = Path(directory) / 'previous.json'
        prior_path.write_bytes(encoded(previous))
        build_site(repo, catalog_path, stage, fingerprints=fingerprints, allowed_signers=allowed_signers,
                   previous_catalog=prior_path, preview_candidate=(None if signed else {'work': work, 'revision': revision}))
        if provisional_repository:
            provisional_settings(stage)
        if not signed:
            # Never export an issuance-shaped SITE manifest for an unsigned
            # proposed edition; PREVIEW.json is its only private inventory.
            (stage / 'SITE.json').unlink()
        inventory = {p.relative_to(stage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in stage.rglob('*') if p.is_file()}
        manifest = {'schema': 'guavajam.preview.v1', 'revision': revision, 'work': work,
                    'origin': origin, 'files': inventory, 'assets': mappings,
                    'publication': False, 'candidate_signed': signed,
                    'base_revision': base, 'provisional_identity': not signed,
                    'provisional_repository': provisional_repository,
                    'repository_checkpoint': checkpoint, 'verified_catalog': verified_catalog}
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
    parser.add_argument('--ref', required=True, help='full commit or explicit local refs/heads/<branch>')
    parser.add_argument('--work', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--base-ref', help='optional ancestor baseline; defaults to main for declared history')
    parser.add_argument('--published', help='required YYYY-MM-DD date for an unsigned proposed edition')
    parser.add_argument('--label', help='optional proposed edition label')
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
                                 require_retention=args.require_retention, published=args.published, label=args.label)
        if args.serve:
            values = credentials(args.repo) if manifest['assets'] else {}
            return serve(args.output.absolute(), manifest, values, port=args.port)
        print(json.dumps({'revision': manifest['revision'], 'preview': True,
                          'assets': len(manifest['assets']), 'public_release': False,
                          'candidate_signed': manifest['candidate_signed'], 'base_revision': manifest['base_revision']}, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('Private preview failed; no publication or public asset state changed.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
