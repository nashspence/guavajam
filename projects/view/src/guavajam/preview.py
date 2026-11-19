"""Assemble the whole proposed GuavaJam site from an exact local Git branch.

Signed candidates use the unmodified production builder and canonical bytes. Unsigned
candidates are provisional catalog overlays rendered by the same site builder;
selected proposed editions remain explicitly provisional. Both are
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
from guavajam_core.catalog import encoded, validate, compare
from guavajam_core.publication_protocol import (discover, resolve, ancestor, publication_history,
                                               commit_facts, run, blob, policy)
from guavajam_core.qualify_assets import qualify
from guavajam_core.resources import ROOT as RESOURCES
from guavajam_core.source_snapshot import snapshot
from .renderer import git_blob
from .site import build_site


def proposed_catalog(catalog, revision, work, facts, manuscript, *, recommendation_locked=False, provisional=()):
    """Add one non-authoritative edition to the reviewed signed catalog.

    The current signed catalog supplies all existing works and editorial settings.
    Ordinary Git metadata describes the draft. Its SHA and edition remain
    explicitly provisional; existing DOI bindings and authority are preserved.
    """
    from guavajam_core.validate_sources import validate_front_matter
    from guavajam_core.identifiers import SLUG
    if not SLUG.fullmatch(work):
        raise AssetError('invalid preview work slug')
    date = facts['published']
    from pathlib import Path
    metadata, _, _, errors = validate_front_matter(Path('works')/work/'manuscript.md', manuscript.decode('utf-8', 'strict'))
    if errors or metadata is None or metadata.get('slug') != work:
        raise AssetError('invalid proposed manuscript metadata')
    item = {'revision': revision, 'published': date, 'message': facts['message'], 'status': 'available'}
    matches = [item for item in catalog['works'] if item['slug'] == work]
    if len(matches) > 1 or (matches and any(e['revision'] == revision for e in matches[0]['editions'])):
        raise AssetError('proposed edition conflicts with a discovered publication')
    number = int(metadata['edition'])
    if number and (not matches or number > max(e.get('edition', 0) for e in matches[0]['editions'])):
        item['edition'] = number
    if matches:
        existing = matches[0]
        original_doi = existing.get('concept_doi')
        proposed_doi = metadata.get('identifiers', {}).get('concept_doi')
        if proposed_doi and proposed_doi != original_doi:
            raise AssetError('changing an issued work DOI requires reviewed repository administration')
        previous_latest = max((e for e in existing['editions'] if e['status'] == 'available'),
                              key=lambda e: (e.get('edition', 0), e['published']), default=None)
        existing['editions'].append(item)
        # Only advance an implicit/default recommendation. An explicitly
        # disabled or older recommendation remains under signed repository control.
        if (not recommendation_locked and previous_latest
                and existing.get('recommended_revision') == previous_latest['revision']):
            existing['recommended_revision'] = revision
    else:
        candidate = {'slug': work, 'metadata_revision': revision, 'recommended_revision': revision,
                     'editions': [item]}
        if metadata.get('identifiers', {}).get('concept_doi'):
            candidate['concept_doi'] = metadata['identifiers']['concept_doi']
        catalog['works'].append(candidate)
        catalog['works'].sort(key=lambda x: x['slug'])
    validate(catalog, provisional=provisional)
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
                  baseline=None):
    """Build the entire hypothetical site, atomically and without public effects.

    A fresh draft repository needs no signer policy or protected branch.
    Publication history always uses independent verification, never a fallback.
    A saved verified baseline prevents session downgrade and lost publications.
    """
    repo = Path(repo).resolve(strict=True)
    revision, events = publication_history(repo, ref, strict=False)
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
    provisional_repository = not events
    selected_event = next((event for sha, event in events if sha == revision), None)
    signed = selected_event is not None and selected_event['kind'] == 'publication'
    if signed and selected_event['manuscript'] != f'works/{work}/manuscript.md':
        raise AssetError('selected signed publication identifies a different work')
    if provisional_repository:
        _, known = publication_history(repo, revision, additional_refs=tuple(preview_refs(repo)), strict=False)
        if known or baseline is not None:
            raise AssetError('preview branch omits known publication history')
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
            raise AssetError('publication history requires independently trusted publisher signing policy')
        refs = preview_refs(repo)
        default_base = next((name for name in ('refs/heads/main', 'refs/remotes/origin/main') if name in refs), revision)
        base = resolve(repo, base_ref or default_base)
        ancestor(repo, base, revision)
        previous, evidence = discover(repo, ref=base, fingerprints=fingerprints, allowed_signers=allowed_signers)
        if signed:
            catalog, evidence = discover(repo, ref=revision, fingerprints=fingerprints,
                                         allowed_signers=allowed_signers, previous=previous)
        else:
            # Draft branches may contain intermediate states that are not valid
            # publication history. Only the reviewed base supplies authority.
            # A newly signed event must be reviewed as part of that base first.
            _, accepted = publication_history(repo, base, strict=False)
            if {sha for sha, _ in events} - {sha for sha, _ in accepted}:
                raise AssetError('unsigned preview requires a base containing its signed publication history')
            if blob(repo, revision, 'guavajam.json') != blob(repo, base, 'guavajam.json'):
                raise AssetError('unsigned preview changes the reviewed repository settings')
            catalog = copy.deepcopy(previous)
        if baseline is not None:
            compare(baseline['catalog'], catalog)
        checkpoint, verified_catalog = evidence['repository_checkpoint'], copy.deepcopy(catalog)
    selected = [work]
    if provisional_repository:
        from guavajam_core.publication_protocol import WORK
        paths = run(repo, 'ls-tree', '-r', '--name-only', revision, '--', 'works', limit=1024*1024).decode('utf-8').splitlines()
        selected = sorted({match[1] for path in paths if (match := WORK.fullmatch(path))})
        if work not in selected or len(selected) > 256:
            raise AssetError('select a work from the bounded committed draft inventory')
    candidates = [{'work': slug, 'revision': revision} for slug in selected] if not signed else None
    provisional = {(item['work'], item['revision']) for item in candidates or []}
    mappings = {}
    for slug in selected:
        for key, mapping in qualify(repo, revision, slug, store=store, require_retention=require_retention).items():
            if key in mappings and mappings[key] != mapping:
                raise AssetError('drafts disagree on private asset identity')
            mappings[key] = mapping
    with snapshot(repo, revision) as (root, _):
        asset_policy = load_policy(root)
        origin = asset_policy.origin if asset_policy else None
    if not signed:
        settings = json.loads(blob(repo, revision, 'guavajam.json'))
        for slug in selected:
            locked = 'recommended_revision' in settings.get('works', {}).get(slug, {})
            catalog = proposed_catalog(catalog, revision, slug, commit_facts(repo, revision),
                                       git_blob(repo, revision, f'works/{slug}/manuscript.md'),
                                       recommendation_locked=locked, provisional=provisional)
    compare(previous, catalog, provisional=provisional)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.guavajam-preview-', dir=output.parent) as directory:
        stage = Path(directory) / 'site'
        catalog_path = Path(directory) / 'catalog.json'
        catalog_path.write_bytes(encoded(catalog))
        prior_path = Path(directory) / 'previous.json'
        prior_path.write_bytes(encoded(previous))
        build_site(repo, catalog_path, stage, fingerprints=fingerprints, allowed_signers=allowed_signers,
                   previous_catalog=prior_path, preview_candidates=candidates)
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
                    'publication': False, 'candidate_signed': signed, 'candidates': candidates or [],
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
    parser.add_argument('--base-ref', help='reviewed publication ancestor; defaults to main for signed history')
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
                          'assets': len(manifest['assets']), 'public_release': False,
                          'candidate_signed': manifest['candidate_signed'], 'base_revision': manifest['base_revision']}, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('Private preview failed; no publication or public asset state changed.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
