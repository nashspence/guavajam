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

from trusted_git import verifier_arguments
from verify_media_sources import media_items

OID = re.compile(r'[0-9a-f]{40}\Z')
GPG_FPR = re.compile(r'(?:[0-9A-F]{40}|[0-9A-F]{64})\Z')
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
    if not isinstance(sha, str) or not OID.fullmatch(sha):
        raise PublicationError('a full lowercase 40-character SHA-1 commit identity is required')
    if git(repo, 'rev-parse', '--verify', sha + '^{commit}').strip().decode() != sha:
        raise PublicationError('revision is not an exact commit identity')
    git(repo,'fsck','--strict','--no-reflogs','--no-dangling',sha)
    raw = git(repo, 'cat-file', '-p', sha)
    try:
        header, message = raw.split(b'\n\n', 1)
        message = message.decode('utf-8', 'strict')
    except (ValueError, UnicodeError) as exc:
        raise PublicationError('commit has no valid UTF-8 publication message') from exc
    if b'\ngpgsig ' not in b'\n' + header:
        raise PublicationError('publication commit must contain a signature')
    parents=re.findall(rb'^parent ([0-9a-f]{40})$',header,re.M)
    tree=re.search(rb'^tree ([0-9a-f]{40})$',header,re.M)
    if len(parents)!=1 or tree is None or git(repo,'rev-parse',parents[0].decode()+'^{tree}').strip()!=tree[1]:
        raise PublicationError('publication must be an empty, single-parent commit preserving reviewed content')
    from publication_protocol import declaration
    try:
        declared = declaration(message)
    except ValueError as exc:
        raise PublicationError('publication trailers must be exact and unique; v1 requires the final two lines: ' + str(exc)) from exc
    if declared is None or declared['kind'] != 'publication':
        raise PublicationError('commit must declare a publication')
    work = declared['manuscript']
    source = git(repo, 'show', sha + ':' + work)
    if len(source) > 8 * 1024 * 1024:
        raise PublicationError('manuscript exceeds source byte limit')
    try:
        source = source.decode('utf-8', 'strict')
    except UnicodeError as exc:
        raise PublicationError('published manuscript must be UTF-8') from exc
    return work, source


def signature(repo: Path, sha: str, *, fingerprints=(), allowed_signers=None):
    if bool(fingerprints) == bool(allowed_signers):
        raise PublicationError('provide exactly one external GPG fingerprint list or SSH allowed-signers file')
    if fingerprints:
        trusted = {f.upper() for f in fingerprints}
        if any(not GPG_FPR.fullmatch(f) for f in trusted):
            raise PublicationError('trusted GPG fingerprints must be full uppercase hex')
        args = ['git','--no-replace-objects',*verifier_arguments(repo,'openpgp'),'verify-commit','--raw',sha]
    else:
        path = Path(allowed_signers).expanduser().resolve(strict=True)
        if path.is_relative_to(repo.resolve()):
            raise PublicationError('SSH allowed-signers authority cannot come from the publication repository')
        if not path.is_file() or path.stat().st_size > 65536:
            raise PublicationError('invalid external SSH allowed-signers file')
        args = ['git','--no-replace-objects',*verifier_arguments(repo,'ssh'),
                '-c', 'gpg.ssh.allowedSignersFile=' + str(path), 'verify-commit', sha]
    p = subprocess.run(args, cwd=repo, capture_output=True, check=False)
    if p.returncode:
        raise PublicationError('commit signature is missing, invalid or not trusted')
    if fingerprints:
        matches = re.findall(rb'\[GNUPG:\] VALIDSIG ([0-9A-Fa-f]{40,64})\b', p.stderr)
        if len(matches) != 1 or matches[0].decode('ascii').upper() not in trusted:
            raise PublicationError('signing fingerprint is not in the independent trust policy')
        return matches[0].decode('ascii').upper()
    return 'external-ssh-allowed-signers'


def verify(repo: Path, sha: str, *, fingerprints=(), allowed_signers=None,
           online_media=True, validate_tree=True, pandoc_version=True, require_retention=False):
    repo = Path(repo).resolve()
    work, source = commit_identity(repo, sha)
    signer = signature(repo, sha, fingerprints=fingerprints, allowed_signers=allowed_signers)
    # Validate the committed object graph, not the mutable checkout or HEAD.
    from source_snapshot import snapshot
    from validate_sources import validate_repository
    with snapshot(repo,sha) as (root,files):
        if work not in {str(file) for file in files}:
            raise PublicationError('committed publication manuscript is missing')
        if validate_tree:
            errors = validate_repository(root, files, enforce_version=pandoc_version)
            if errors:
                raise PublicationError('published GFM violates Press conventions: ' + '; '.join(errors[:8]))
        from asset_policy import load_policy
        policy = load_policy(root)
        items = media_items(root, Path(work))
        if online_media:
            from verify_source_assets import verify_source_assets
            verify_source_assets(root, paths=[f for f in files if f.is_relative_to(Path(work).parent)], require_retention=require_retention)
        from supporting_gfm import record_path,load_gfm_record
        documents={}
        for relative in files:
            if relative.parent==Path(work).parent/'supporting' and record_path(relative):
                _,positions,*_=load_gfm_record(root/relative,root=root);documents[str(relative)]=sorted(positions)
        if (root / work).read_text(encoding='utf-8') != source:
            raise PublicationError('Git snapshot did not reproduce publication source')
    from validate_sources import validate_front_matter, inspect_manuscript_syntax
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
