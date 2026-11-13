#!/usr/bin/env python3
"""Verify a Press edition directly from its *signed Git commit*.

No tag, release, account, URL or rendered HTML is part of edition identity.
Trust anchors must be supplied externally to the source commit under review.
The command never signs, publishes, uploads or mutates a repository.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from .trusted_git import verify_signature as trusted_signature
from .verify_media_sources import media_items

OID = re.compile(r'[0-9a-f]{40}\Z')
PUBLICATION = re.compile(r'works/([a-z0-9]+(?:-[a-z0-9]+)*)/manuscript\.md\Z')
IDENT = re.compile(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z')


class PublicationError(ValueError):
    pass


def git(repo, *arguments, env=None):
    process = subprocess.run(['git', '--no-replace-objects', *arguments], cwd=repo,
                             capture_output=True, env=env, check=False)
    if process.returncode:
        raise PublicationError('Git revision or verification operation failed')
    return process.stdout


def commit_identity(repo: Path, sha: str):
    from .publication_protocol import resolve, verify_shape, blob, run
    try:
        if resolve(repo, sha) != sha:
            raise ValueError('revision is not an exact commit identity')
        run(repo, 'fsck', '--strict', '--no-reflogs', '--no-dangling', sha)
        declared = verify_shape(repo, sha, 'publication')
        work = declared['manuscript']
        source = blob(repo, sha, work, maximum=8 * 1024 * 1024).decode('utf-8', 'strict')
    except (ValueError, OSError) as exc:
        raise PublicationError(str(exc)) from exc
    return work, source


def signature(repo: Path, sha: str, *, fingerprints=(), allowed_signers=None):
    try:
        return trusted_signature(repo, sha, fingerprints=fingerprints, allowed_signers=allowed_signers)
    except (ValueError, OSError) as exc:
        raise PublicationError(str(exc)) from exc


def verify(repo: Path, sha: str, *, fingerprints=(), allowed_signers=None,
           online_media=True, validate_tree=True, pandoc_version=True, require_retention=False):
    repo = Path(repo).resolve()
    work, source = commit_identity(repo, sha)
    signer = signature(repo, sha, fingerprints=fingerprints, allowed_signers=allowed_signers)
    # Validate the committed object graph, not the mutable checkout or HEAD.
    from .source_snapshot import snapshot
    from .validate_sources import validate_repository
    with snapshot(repo,sha) as (root,files):
        if work not in {str(file) for file in files}:
            raise PublicationError('committed publication manuscript is missing')
        if validate_tree:
            errors = validate_repository(root, files, enforce_version=pandoc_version)
            if errors:
                raise PublicationError('published GFM violates Press conventions: ' + '; '.join(errors[:8]))
        from .asset_policy import load_policy
        policy = load_policy(root)
        items = media_items(root, Path(work))
        if online_media:
            from .verify_source_assets import verify_source_assets
            verify_source_assets(root, paths=[f for f in files if f.is_relative_to(Path(work).parent)], require_retention=require_retention)
        from .supporting_gfm import record_path,load_gfm_record
        documents={}
        for relative in files:
            if relative.parent==Path(work).parent/'supporting' and record_path(relative):
                _,positions,*_=load_gfm_record(root/relative,root=root);documents[str(relative)]=sorted(positions)
        if (root / work).read_text(encoding='utf-8') != source:
            raise PublicationError('Git snapshot did not reproduce publication source')
    from .validate_sources import validate_front_matter, inspect_manuscript_syntax
    _, body, line, _ = validate_front_matter(Path(work),source)
    info, _ = inspect_manuscript_syntax(Path(work),body,line)
    anchors = info['anchors'];documents[work]=sorted(anchors)
    return {
        'anchors': sorted(anchors),
        'documents': documents,
        'assets_verified': online_media,
        'media_verified': (not items or online_media),
        'schema': 'press.citation.v1',
        'hash_algorithm': 'sha1',
        'revision': sha,
        'manuscript': work,
        'signer': signer,
        'media': [{key: item[key] for key in ('kind', 'id', 'src', 'transcript')}
                  for item in items],
    }


def citation(record, anchor=None, document=None):
    document=document or record['manuscript']
    if document not in record.get('documents',{record['manuscript']:record.get('anchors',[])}):raise PublicationError('citation document must be a reviewed source within the published work')
    anchors=record.get('documents',{}).get(document,record.get('anchors',[]))
    if anchor is not None and (not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',anchor) or anchor not in anchors):raise PublicationError('citation fragment must identify an existing stable source anchor')
    result={key:record[key] for key in ('schema','hash_algorithm','revision','manuscript')}
    if document!=record['manuscript']:result['document']=document
    if anchor:result['anchor']=anchor
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default='.')
    parser.add_argument('--revision', required=True, help='full Git commit SHA-1, never branch or tag')
    trust = parser.add_mutually_exclusive_group(required=True)
    trust.add_argument('--trusted-gpg-fingerprint', action='append', dest='fingerprints')
    trust.add_argument('--ssh-allowed-signers')
    parser.add_argument('--offline', action='store_true', help='inspect signature and source only; cannot qualify publication assets')
    parser.add_argument('--anchor')
    parser.add_argument('--document',help='full reviewed supporting-document path in the published work')
    args = parser.parse_args(argv)
    try:
        result = verify(Path(args.repo), args.revision,
                        fingerprints=args.fingerprints or (), allowed_signers=args.ssh_allowed_signers,
                        online_media=not args.offline)
        result['citation'] = citation(result,args.anchor,args.document)
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    except (PublicationError, ValueError, OSError, subprocess.SubprocessError) as exc:
        print('Publication verification failed: ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
