"""Optional collection view: a public repository directory plus independent trust."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from html import escape
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import uuid

import yaml
from guavajam_core.catalog import CatalogError, encoded, https, validate
from guavajam_core.publication_protocol import discover, run
from .site import build_site, page
from .update import digest, locked_root, read_json, renderer_identity, trust_identity, verify_public_delivery

MAX_BYTES = 1024 * 1024
MAX_REPOSITORIES = 256


def yaml_data(raw):
    if len(raw) > MAX_BYTES:
        raise CatalogError('index or trust file exceeds 1 MiB')
    try:
        for token in yaml.scan(raw):
            if isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken, yaml.tokens.TagToken)):
                raise CatalogError('index and trust YAML must not contain aliases, anchors or tags')
        class Loader(yaml.SafeLoader):
            pass
        def mapping(loader, node):
            result = {}
            for key_node, value_node in node.value:
                key = loader.construct_object(key_node, deep=True)
                if not isinstance(key, str) or key in result:
                    raise CatalogError('duplicate or non-string YAML key')
                result[key] = loader.construct_object(value_node, deep=True)
            return result
        Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
        return yaml.load(raw, Loader=Loader)
    except (yaml.YAMLError, UnicodeError, RecursionError) as exc:
        raise CatalogError('invalid index or trust YAML') from exc


def read_index(source):
    source = str(source)
    if source.startswith('https://'):
        https(source, 'index URL')
        class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                https(newurl, 'index redirect')
                return super().redirect_request(req, fp, code, msg, headers, newurl)
        opener = urllib.request.build_opener(HTTPSRedirect())
        with opener.open(source, timeout=30) as response:
            raw = response.read(MAX_BYTES + 1)
    else:
        with Path(source).open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
    data = yaml_data(raw)
    if not isinstance(data, dict) or set(data) != {'repositories'}:
        raise CatalogError('index requires only a repositories list')
    urls = data['repositories']
    if not isinstance(urls, list) or len(urls) > MAX_REPOSITORIES:
        raise CatalogError('index requires a bounded repositories list')
    result = []
    for url in urls:
        https(url, 'repository URL')
        if url in result:
            raise CatalogError('duplicate repository URL')
        result.append(url)
    return result


def read_trust(path):
    path = Path(path).resolve(strict=True)
    with path.open('rb') as stream:
        data = yaml_data(stream.read(MAX_BYTES + 1))
    if not isinstance(data, dict):
        raise CatalogError('operator trust must map repository URLs to independent signer policies')
    for url, policy in data.items():
        https(url, 'trusted repository URL')
        if not isinstance(policy, dict) or set(policy) not in ({'allowed_signers'}, {'fingerprints'}, {'fingerprints', 'public_keys'}):
            raise CatalogError('each repository requires exactly one GPG or SSH trust policy')
        if 'fingerprints' in policy:
            if not isinstance(policy['fingerprints'], list) or not policy['fingerprints'] or not all(
                    isinstance(f, str) and re.fullmatch(r'[A-Fa-f0-9]{40}|[A-Fa-f0-9]{64}', f) for f in policy['fingerprints']):
                raise CatalogError('invalid independent publisher fingerprints')
        for key in ('allowed_signers', 'public_keys'):
            if key in policy:
                candidate = policy[key]
                if not isinstance(candidate, str) or not candidate:
                    raise CatalogError('trust material requires an operator-owned file path')
                candidate_path = Path(candidate)
                if not candidate_path.is_absolute():
                    if '..' in candidate_path.parts or '\\' in candidate:
                        raise CatalogError('relative trust path must stay within the index repository')
                    candidate_path = (path.parent / candidate_path).resolve(strict=True)
                    if not candidate_path.is_relative_to(path.parent):
                        raise CatalogError('relative trust path must stay within the index repository')
                policy[key] = str(candidate_path)
    return data


def prepare_source(root, url):
    """Fetch data only; never checkout, recurse into submodules, or run source code."""
    identifier = digest(url.encode())
    parent = root / '.sources'
    if parent.is_symlink():
        raise CatalogError('source cache must not be a symlink')
    parent.mkdir(mode=0o700, exist_ok=True)
    repo = parent / identifier
    if repo.is_symlink():
        raise CatalogError('source clone must not be a symlink')
    if not repo.exists():
        temporary = parent / ('.clone-' + uuid.uuid4().hex)
        try:
            result = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'protocol.ext.allow=never',
                                     'clone', '--bare', '--single-branch', '--branch', 'main', '--no-tags',
                                     '--', url, str(temporary)], capture_output=True, timeout=300, check=False)
            if result.returncode:
                raise CatalogError('repository clone failed')
            temporary.rename(repo)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    else:
        configured = run(repo, 'config', '--get', 'remote.origin.url', limit=8192).decode().strip()
        if configured != url:
            raise CatalogError('cached repository URL differs')
        run(repo, '-c', 'protocol.ext.allow=never', 'fetch', '--atomic', '--no-tags',
            '--no-recurse-submodules', 'origin', 'refs/heads/main:refs/heads/main', limit=65536)
    return repo


@contextmanager
def signing_policy(root, repo, url, configured):
    policy = dict(configured)
    public_keys = policy.pop('public_keys', None)
    previous = os.environ.get('GNUPGHOME')
    try:
        if public_keys:
            keys = Path(public_keys).resolve(strict=True)
            if not keys.is_file() or keys.stat().st_size > MAX_BYTES or keys.is_relative_to(repo):
                raise CatalogError('GPG keys must be bounded external operator material')
            home = root / '.keys' / digest(url.encode())
            if home.is_symlink() or home.resolve() != home.absolute():
                raise CatalogError('keyring path must not traverse symlinks')
            home.mkdir(parents=True, mode=0o700, exist_ok=True)
            os.environ['GNUPGHOME'] = str(home)
            result = subprocess.run(['gpg', '--batch', '--import', str(keys)], capture_output=True, timeout=30, check=False)
            if result.returncode:
                raise CatalogError('independent GPG key import failed')
        yield policy
    finally:
        if previous is None:
            os.environ.pop('GNUPGHOME', None)
        else:
            os.environ['GNUPGHOME'] = previous


def retained(root):
    link = root / 'current'
    if not link.is_symlink():
        if link.exists() or any((root / 'generations').iterdir()):
            raise CatalogError('restore the retained collection baseline before updating')
        return None
    target = os.readlink(link)
    if not re.fullmatch(r'generations/[a-f0-9]{32}/site', target):
        raise CatalogError('invalid collection generation link')
    site = root / target
    if site.resolve() != site or not site.is_dir():
        raise CatalogError('collection generation is missing or redirected')
    receipt = read_json(site.parent / 'STATE.json', maximum=64 * MAX_BYTES)
    if receipt.get('schema') != 'guavajam.index-state.v1' or digest((site / 'INDEX.json').read_bytes()) != receipt.get('index_sha256'):
        raise CatalogError('collection baseline differs from its retained receipt')
    return receipt


def update_index(index, trust_file, output):
    """Switch the whole collection only after every selected source verifies.

    Baselines survive removal from the public directory, so adding a repository
    again cannot erase its previously observed publication history.
    """
    urls = read_index(index)
    policies = read_trust(trust_file)
    if any(url not in policies for url in urls):
        raise CatalogError('every indexed repository needs independently configured signer trust')
    with locked_root(output, marker_text='guavajam.index-root.v1\n') as root:
        if root is None:
            return {'status': 'busy', 'changed': False}
        old = retained(root)
        catalogs = dict(old['catalogs']) if old else {}
        sources = []
        renderer = renderer_identity()
        for url in urls:
            repo = prepare_source(root, url)
            with signing_policy(root, repo, url, policies[url]) as policy:
                catalog, _ = discover(repo, fingerprints=policy.get('fingerprints', ()),
                                      allowed_signers=policy.get('allowed_signers'), previous=catalogs.get(url))
                trusted = trust_identity(policy.get('fingerprints', ()), policy.get('allowed_signers'))
                sources.append((url, repo, catalog, trusted))
        identity = digest(encoded({'index': urls, 'catalogs': {u: c for u, _, c, _ in sources},
                                   'renderer': renderer, 'trust': {u: t for u, _, _, t in sources}}))
        if old and old['identity'] == identity:
            return {'status': 'unchanged', 'changed': False, 'repositories': len(urls)}
        generation = uuid.uuid4().hex
        with tempfile.TemporaryDirectory(prefix='.collection-', dir=root) as temporary:
            stage = Path(temporary) / 'generation'; stage.mkdir()
            site = stage / 'site'; site.mkdir()
            entries = []
            rows = []
            for url, repo, catalog, trusted in sources:
                identifier = digest(url.encode())
                with signing_policy(root, repo, url, policies[url]) as policy:
                    verify_public_delivery(repo, catalog, previous=catalogs.get(url), **policy)
                    manifest = build_site(repo, site / 'sources' / identifier, ref=catalog['tip'],
                                          previous=catalogs.get(url), **policy,
                                          view_cache=root / '.cache' / digest((renderer + trusted + identifier).encode()))
                catalogs[url] = catalog
                entries.append({'repository': url, 'path': 'sources/' + identifier, 'tip': catalog['tip']})
                rows.append('<li><a href="sources/' + identifier + '/index.html">' + escape(manifest['title']) +
                            '</a> — <a href="' + escape(url, quote=True) + '">Source repository</a></li>')
            (site / 'index.html').write_text(page('Publications', '<ul>' + ''.join(rows) + '</ul>'), encoding='utf-8')
            public = {'schema': 'guavajam.index-view.v1', 'repositories': entries}
            (site / 'INDEX.json').write_bytes(encoded(public))
            receipt = {'schema': 'guavajam.index-state.v1', 'identity': identity, 'catalogs': catalogs,
                       'index_sha256': digest(encoded(public))}
            (stage / 'STATE.json').write_bytes(encoded(receipt))
            for path in stage.rglob('*'):
                if path.is_file():
                    with path.open('rb') as stream:
                        os.fsync(stream.fileno())
            stage.rename(root / 'generations' / generation)
            descriptor = os.open(root / 'generations', os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        pending = root / ('.current-' + generation)
        try:
            pending.symlink_to('generations/' + generation + '/site')
            os.replace(pending, root / 'current')
            descriptor = os.open(root, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            pending.unlink(missing_ok=True)
        return {'status': 'updated', 'changed': True, 'repositories': len(urls), 'site': str(root / 'current')}


def build_pages(index, trust_file, output, previous_state, state_output):
    """Produce static Pages files plus a separately retained history checkpoint.

    No publisher code, Makefile, hooks, source program or working tree is run.
    The output directory must be new and the checkpoint must be outside it.
    """
    urls = read_index(index)
    policies = read_trust(trust_file)
    if any(url not in policies for url in urls):
        raise CatalogError('every indexed repository requires independent signer trust')
    previous = read_json(Path(previous_state), maximum=64 * MAX_BYTES)
    if not isinstance(previous, dict) or set(previous) != {'schema', 'catalogs'} or previous['schema'] != 'guavajam.index-checkpoint.v1':
        raise CatalogError('Pages requires an independently retained verification checkpoint')
    catalogs = previous['catalogs']
    if not isinstance(catalogs, dict) or len(catalogs) > 1024:
        raise CatalogError('invalid retained collection')
    for url, catalog in catalogs.items():
        https(url, 'retained repository URL')
        validate(catalog)
    destination = Path(output).absolute()
    state_file = Path(state_output).absolute()
    if destination.exists() or destination.is_symlink() or destination.resolve() != destination:
        raise CatalogError('Pages output must be a new directory without symlink ancestors')
    if (state_file.exists() or state_file.is_symlink() or state_file.resolve() != state_file
            or state_file.is_relative_to(destination)):
        raise CatalogError('Pages checkpoint must be a new file outside public HTML')
    next_catalogs = dict(catalogs)
    with tempfile.TemporaryDirectory(prefix='guavajam-pages-') as temporary:
        cache = Path(temporary)
        staging = cache / 'site'
        staging.mkdir()
        entries, rows = [], []
        for url in urls:
            repo = prepare_source(cache, url)
            with signing_policy(cache, repo, url, policies[url]) as policy:
                prior = catalogs.get(url)
                catalog, _ = discover(repo, fingerprints=policy.get('fingerprints', ()),
                                      allowed_signers=policy.get('allowed_signers'), previous=prior)
                verify_public_delivery(repo, catalog, previous=prior, **policy)
                path = 'sources/' + digest(url.encode())
                manifest = build_site(repo, staging / path, ref=catalog['tip'], previous=prior, **policy)
            next_catalogs[url] = catalog
            entries.append({'repository': url, 'path': path, 'tip': catalog['tip']})
            rows.append('<li><a href="' + path + '/index.html">' + escape(manifest['title']) +
                        '</a> — <a href="' + escape(url, quote=True) + '">Source repository</a></li>')
        (staging / 'index.html').write_text(page('Publications', '<ul>' + ''.join(rows) + '</ul>'), encoding='utf-8')
        (staging / 'INDEX.json').write_bytes(encoded({'schema': 'guavajam.index-view.v1', 'repositories': entries}))
        next_state = {'schema': 'guavajam.index-checkpoint.v1', 'catalogs': next_catalogs}
        state_file.parent.mkdir(parents=True, exist_ok=True)
        with state_file.open('xb') as stream:
            stream.write(encoded(next_state))
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(destination)
    return {'repositories': len(urls), 'output': str(destination), 'checkpoint': str(state_file)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', required=True)
    parser.add_argument('--trust', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--pages', action='store_true', help='Build a static GitHub Pages artifact')
    parser.add_argument('--previous-state', type=Path)
    parser.add_argument('--state-output', type=Path)
    args = parser.parse_args(argv)
    if args.pages and (not args.previous_state or not args.state_output):
        parser.error('--pages requires --previous-state and --state-output')
    if not args.pages and (args.previous_state or args.state_output):
        parser.error('checkpoint arguments require --pages')
    try:
        result = (build_pages(args.index, args.trust, args.output, args.previous_state, args.state_output)
                  if args.pages else update_index(args.index, args.trust, args.output))
        print(json.dumps(result, sort_keys=True))
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print('Collection update failed: ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
