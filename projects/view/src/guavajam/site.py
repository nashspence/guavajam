"""Derive a script-free edition directory from one independently verified work."""
from __future__ import annotations

import argparse
import hashlib
from html import escape
import json
from pathlib import Path
import sys
import tempfile

from guavajam_core.catalog import CatalogError, encoded, read_catalog
from guavajam_core.publication_protocol import discover
from guavajam_core.publication_commit import citation, verify
from .renderer import git_blob, render
from .view_cache import cached_render


def page(title, body, *, search=False):
    styles = Path(__file__).parent / 'styles'
    css = (styles / 'reading.css').read_text('utf-8') + '\n' + (styles / 'catalog.css').read_text('utf-8')
    if search:
        css += ("\n.guavajam-index-search { padding: 1rem; border: 1px solid var(--guavajam-border); margin-block: 1.5rem; }"
                "\n#guavajam-search { display: flex; flex-wrap: wrap; align-items: end; gap: .7rem; }"
                "\n#guavajam-search > div { min-width: 0; max-width: 100%; }"
                "\n#guavajam-search label { display: block; font: 600 .85rem/1.3 system-ui, sans-serif; }"
                "\n#guavajam-search input, #guavajam-search select, #guavajam-search button,"
                " #guavajam-search-prev, #guavajam-search-next { font: inherit; padding: .5rem; max-width: 100%; box-sizing: border-box; }"
                "\n#guavajam-search-results li, #guavajam-static-list li { border-bottom: 1px solid var(--guavajam-border); padding-block: .7rem; }"
                "\n.guavajam-search-meta { color: var(--guavajam-muted); font-size: .88rem; }"
                "\n[hidden] { display: none !important; }")
    script = ("script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; connect-src 'self'; "
              if search else "script-src 'none'; ")
    form_action = "form-action 'self'" if search else "form-action 'none'"
    return ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
            'style-src \'unsafe-inline\'; img-src data:; ' + script +
            'base-uri \'none\'; object-src \'none\'; ' + form_action + '\">'
            '<title>' + escape(title) + ' — GuavaJam</title><style>' + css + '</style></head>'
            '<body><a class="guavajam-skip" href="#main">Skip to content</a>'
            '<main id="main" tabindex="-1"><h1>' + escape(title) + '</h1>' + body + '</main>'
            '<footer><p>Each edition is identified by its signed Git commit. '
            'This website is a disposable presentation.</p></footer></body></html>\n')


def inventory(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()}


def edition_title(edition):
    return edition['message'].splitlines()[0] if edition['message'].splitlines() else edition['revision'][:12]


def _build(repo, catalog, output, *, fingerprints, allowed_signers, view_cache,
           repository, search_assets):
    from .navigation import document_directory
    from .work_search import build_work_index, publication_navigation
    output.mkdir()
    latest_title = 'Unpublished work'
    rows = []
    for edition in catalog['editions']:
        sha = edition['revision']
        directory = output / 'editions' / sha
        directory.mkdir(parents=True)
        reader = directory / 'read'
        proof = verify(repo, sha, fingerprints=fingerprints, allowed_signers=allowed_signers, online_media=False)
        if view_cache:
            cached_render(view_cache, repo, sha, reader, fingerprints=fingerprints, allowed_signers=allowed_signers)
        else:
            render(repo, sha, reader, fingerprints=fingerprints, allowed_signers=allowed_signers)
        view = json.loads((reader / 'VIEW.json').read_text('utf-8'))
        publication_navigation(reader, view, sha)
        navigation = json.loads((reader / 'NAV.json').read_text('utf-8'))
        latest_title = view['facts']['title']
        for document, details in view['documents'].items():
            source = git_blob(repo, sha, document)
            destination = reader / Path(details['view']).parent / (Path(details['view']).stem + '.gfm')
            if document == 'content/manuscript.md':
                destination = reader / 'source.gfm'
            destination.write_bytes(source)
            cited = citation(proof, document=document)
            cited_path = destination.with_suffix('.citation.json')
            cited_path.write_bytes(encoded(cited))
        (directory / 'SOURCE.json').write_bytes(encoded(proof))
        (directory / 'CITATION.json').write_bytes(encoded(citation(proof)))
        summary = edition_title(edition)
        body = ('<nav aria-label="Breadcrumb"><a href="../../index.html">' + escape(latest_title) + '</a></nav>'
                '<p><time datetime="' + edition['published'] + '">' + edition['published'] + '</time></p>'
                '<p><code>' + sha + '</code></p><pre class="guavajam-message">' + escape(edition['message']) + '</pre>'
                '<p><a href="read/index.html">Read this edition</a> · '
                '<a href="read/source.gfm" download>Download exact Markdown</a> · '
                '<a href="CITATION.json">Source citation</a> · '
                '<a href="SOURCE.json">Verification record</a></p>')
        body += ('<h2>Documents in this edition</h2>' + document_directory(navigation, prefix='read/') +
                 '<p><a href="read/documents.html">Browse contents</a> · '
                 '<a href="../../browse/index.html?edition=' + sha + '">Search this edition</a></p>')
        (directory / 'index.html').write_text(page(summary, body), encoding='utf-8')
        rows.append('<li><a href="editions/' + sha + '/index.html">' + escape(summary) + '</a> '
                    '<time datetime="' + edition['published'] + '">' + edition['published'] + '</time> '
                    '<code>' + sha[:12] + '</code></li>')
    if rows:
        latest = catalog['editions'][-1]['revision']
        body = ('<p><a href="editions/' + latest + '/read/index.html">Read latest edition</a> · '
                '<a href="editions/' + latest + '/read/documents.html">All documents and contents</a></p>'
                '<p><a href="browse/index.html">Browse and search this work</a></p>'
                '<h2>Edition history</h2><p><a href="browse/index.html?kind=editions">'
                'Search or page through editions</a></p><ol reversed>' + ''.join(reversed(rows)) + '</ol>')
    else:
        body = '<p>No signed editions have been published on the selected history.</p>'
    body += '<p><a href="CATALOG.json">Verified Git history</a></p>'
    (output / 'index.html').write_text(page(latest_title, body), encoding='utf-8')
    (output / 'CATALOG.json').write_bytes(encoded(catalog))
    build_work_index(output, catalog, repository=repository, search_assets=search_assets)
    manifest = {'schema': 'guavajam.site.v1', 'title': latest_title, 'tip': catalog['tip'],
                'publications': [e['revision'] for e in catalog['editions']], 'files': inventory(output)}
    (output / 'SITE.json').write_bytes(encoded(manifest))
    return manifest


def build_site(repo, output, *, ref='refs/heads/main', fingerprints=(), allowed_signers=None,
               previous=None, view_cache=None, repository='', search_assets=None):
    """Discover Git directly; a catalog supplied by a website never authorizes editions."""
    repo = Path(repo).resolve(strict=True)
    output = Path(output).absolute()
    if output.exists() or output.is_symlink() or output.resolve() != output or output.is_relative_to(repo):
        raise CatalogError('site output must be new, outside the source repository, without symlink ancestors')
    if isinstance(previous, (str, Path)):
        previous = read_catalog(previous)
    catalog, _ = discover(repo, ref=ref, fingerprints=fingerprints, allowed_signers=allowed_signers, previous=previous)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.guavajam-site-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'site'
        manifest = _build(repo, catalog, stage, fingerprints=fingerprints,
                          allowed_signers=allowed_signers, view_cache=view_cache,
                          repository=repository, search_assets=search_assets)
        stage.rename(output)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--ref', default='refs/heads/main')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--previous-catalog', type=Path)
    trust = parser.add_mutually_exclusive_group(required=True)
    trust.add_argument('--trusted-gpg-fingerprint', action='append', default=[])
    trust.add_argument('--ssh-allowed-signers', type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = build_site(args.repo, args.output, ref=args.ref, previous=args.previous_catalog,
                              fingerprints=args.trusted_gpg_fingerprint, allowed_signers=args.ssh_allowed_signers)
    except (ValueError, OSError) as exc:
        print('GuavaJam site failed: ' + str(exc), file=sys.stderr)
        return 1
    print('Wrote', len(manifest['publications']), 'edition(s).')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
