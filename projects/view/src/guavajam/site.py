"""Derive a script-free edition directory from one independently verified work."""
from __future__ import annotations

import argparse
import hashlib
from html import escape
import json
import re
from pathlib import Path
import sys
import tempfile

from guavajam_core.catalog import CatalogError, encoded, read_catalog
from guavajam_core.publication_protocol import discover
from guavajam_core.publication_commit import citation, verify
from .renderer import git_blob, render
from .view_cache import cached_render


def detail_link(href, label):
    """An unobtrusive, accessible route from a reading title to its record."""
    return (' <a class="guavajam-record-link" href="' + escape(href, quote=True) +
            '" aria-label="' + escape(label, quote=True) + '" title="' +
            escape(label, quote=True) + '"><svg viewBox="0 0 24 24" '
            'aria-hidden="true" focusable="false"><path d="M6 3.5h9l3 3V20.5H6z"/>'
            '<path d="M15 3.5v4h4M9 12h6M9 15h6"/></svg></a>')


def citation_control(record, href, *, title='', authors='', published='', edition_number=None,
                     document_title=''):
    """Readable Git-commit citation; keep canonical machine JSON downloadable.

    The bibliographic display uses verified manuscript facts and the signed
    edition's claimed date. The repository commit remains the exact identity;
    no HTML URL is treated as publication authority.
    """
    if record is None:
        return ''
    revision = record['revision']
    manuscript = record.get('manuscript', '')
    document = record.get('document', manuscript)
    subject = document_title or title
    description = (escape(authors) + '. ' if authors else '')
    if subject:
        description += '<cite>' + escape(subject) + '</cite>'
        if document_title and title and document_title != title:
            description += '. In <cite>' + escape(title) + '</cite>'
        description += '.'
    details = []
    if edition_number is not None:
        details.append(edition_label(edition_number))
    if published:
        details.append(escape(published))
    if details:
        description += (' ' if description else '') + ', '.join(details) + '.'
    source = ('Git commit <code>' + escape(revision) + '</code>' +
              (' · <code>' + escape(document) + '</code>' if document else '') +
              (' · <code>#' + escape(record['anchor']) + '</code>' if record.get('anchor') else ''))
    return ('<blockquote class="guavajam-citation" aria-label="Git source citation">'
            + ('<p>' + description + '</p>' if description else '') +
            '<p class="guavajam-citation-source">' + source + '</p>' +
            '<a href="' + escape(href, quote=True) + '" download '
            'aria-label="Download official citation JSON" title="Download citation JSON">'
            '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
            '<path d="M12 3v12m-4-4 4 4 4-4M5 17v4h14v-4"/></svg></a></blockquote>')


def page(title, body, *, search=False, ancestors=(), breadcrumb_current=None,
         citation_record=None, citation_href=None, citation_title=None, citation_authors=None,
         citation_published=None, citation_edition=None, atom_href=None):
    """Use the breadcrumb itself as the one page heading, above the citation."""
    styles = Path(__file__).parent / 'styles'
    css = (styles / 'reading.css').read_text('utf-8') + '\n' + (styles / 'catalog.css').read_text('utf-8')
    script = ("script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; connect-src 'self'; "
              if search else "script-src 'none'; ")
    form_action = "form-action 'self'" if search else "form-action 'none'"
    parts = ['<li><a href="' + escape(href, quote=True) + '">' + escape(label) + '</a></li>'
             for href, label in ancestors]
    parts.append('<li><h1 aria-current="page">' + escape(breadcrumb_current or title) + '</h1></li>')
    crumb = ('<nav class="guavajam-navigation" aria-label="Breadcrumb" tabindex="0"><ol>' +
             ''.join(parts) + '</ol></nav>')
    citation_html = (citation_control(citation_record, citation_href, title=citation_title or title,
                                     authors=citation_authors or '', published=citation_published or '',
                                     edition_number=citation_edition) if citation_href else '')
    feed_html = (' <a class="guavajam-feed-link" href="' + escape(atom_href, quote=True) +
                 '" aria-label="Publication updates (Atom)" title="Publication updates (Atom)">'
                 '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
                 '<circle cx="5" cy="19" r="1.5"/><path d="M4 11a9 9 0 0 1 9 9M4 4a16 16 0 0 1 16 16"/>'
                 '</svg></a>' if atom_href else '')
    return ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
            'style-src \'unsafe-inline\'; img-src data:; ' + script +
            'base-uri \'none\'; object-src \'none\'; ' + form_action + '">'
            '<title>' + escape(title) + (' — GuavaJam' if title != 'GuavaJam' else '') + '</title><style>' + css + '</style></head>'
            '<body class="guavajam-site"><a class="guavajam-skip" href="#main">Skip to content</a>'
            + crumb + '<main id="main" tabindex="-1">' + citation_html + body + '</main>'
            '<footer><p>GuavaJam' + feed_html + '</p></footer></body></html>\n')


def edition_label(position):
    """The verified first-parent sequence, not a publisher-claimed timestamp."""
    return 'Edition ' + str(position)


def inventory(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()}



def committer_identity(repo, revision):
    """Read the display byline from the immutable signed Git commit header.

    This is a presentation detail, not an edition ordering or signing claim.
    """
    from guavajam_core.publication_protocol import commit
    header, _ = commit(repo, revision)
    match = re.search(rb'^committer (.+) <([^<>\r\n]*)> -?\d+ [+-]\d{4}$', header, re.M)
    if not match:
        return {'name': '', 'email': ''}
    name = match[1].decode('utf-8', 'replace')[:512]
    email = match[2].decode('utf-8', 'replace')[:320]
    # Git permits arbitrary strings for an email header; only render a safe
    # mailto link if it genuinely resembles an address.
    if not re.fullmatch(r'[^\s<>@/?#]+@[^\s<>@/?#]+', email):
        email = ''
    return {'name': name, 'email': email}


def _build(repo, catalog, output, *, fingerprints, allowed_signers, view_cache,
           repository, search_assets):
    from .work_search import build_work_index, publication_navigation
    from guavajam_core.bibliography import ordered_authors, author_text
    output.mkdir()
    latest_title = 'Unpublished work'
    latest_facts = {}
    edition_rows = []
    in_collection = search_assets is not None and bool(repository)
    work_crumbs = (('../../index.html', 'GuavaJam'),) if in_collection else ()
    for position, edition in enumerate(catalog['editions'], 1):
        sha = edition['revision']
        directory = output / 'editions' / sha
        directory.mkdir(parents=True)
        reader = directory / 'read'
        proof = verify(repo, sha, fingerprints=fingerprints, allowed_signers=allowed_signers,
                       online_media=False)
        if view_cache:
            cached_render(view_cache, repo, sha, reader, fingerprints=fingerprints,
                          allowed_signers=allowed_signers)
        else:
            render(repo, sha, reader, fingerprints=fingerprints, allowed_signers=allowed_signers)
        view = json.loads((reader / 'VIEW.json').read_text('utf-8'))
        latest_title = view['facts']['title']
        latest_facts = view['facts']
        metadata = committer_identity(repo, sha)
        (directory / 'COMMITTER.json').write_bytes(encoded(metadata))
        (directory / 'SOURCE.json').write_bytes(encoded(proof))
        (directory / 'CITATION.json').write_bytes(encoded(citation(proof)))
        for document, details in view['documents'].items():
            source = git_blob(repo, sha, document)
            destination = reader / Path(details['view']).parent / (Path(details['view']).stem + '.gfm')
            if document == 'content/manuscript.md':
                destination = reader / 'source.gfm'
            destination.write_bytes(source)
            cited = citation(proof, document=document)
            destination.with_suffix('.citation.json').write_bytes(encoded(cited))
        publication_navigation(reader, view, sha, work_title=latest_title,
                               edition_number=position, published=edition['published'],
                               in_collection=in_collection)
        names = author_text(ordered_authors(view['facts']), identifiers=False)
        abstract = view['facts'].get('abstract', '')
        ed_label = edition_label(position)
        body = ''
        if abstract:
            body += '<p class="guavajam-abstract">' + escape(abstract) + '</p>'
        body += ('<p class="guavajam-actions"><a href="read/index.html">Read</a>' +
                 '<a href="CITATION.json" download>Cite</a>' +
                 '<a href="read/source.gfm" download>Download</a>' +
                 '<a href="read/documents.html">Documents</a>' +
                 '<a href="../../browse/index.html?edition=' + sha +
                 '">Search</a></p>' +
                 '<details class="guavajam-provenance"><summary>Source and verification</summary>' +
                 '<p>Signed Git revision: <code>' + sha + '</code></p>' +
                 '<p class="guavajam-message">Commit message: ' +
                 escape(edition['message']) + '</p>' +
                 '<p><a href="SOURCE.json">Verification record</a></p></details>')
        edition_crumbs = ((('../../../../index.html', 'GuavaJam'),) if in_collection else ()) + (
            ('../../index.html', latest_title),)
        (directory / 'index.html').write_text(
            page(latest_title + ' — ' + ed_label, body, ancestors=edition_crumbs,
                 breadcrumb_current=ed_label, citation_record=citation(proof),
                 citation_href='CITATION.json', citation_title=latest_title, citation_authors=names,
                 citation_published=edition['published'], citation_edition=position), encoding='utf-8')
        edition_rows.append((sha, ed_label, edition['published'], edition['message']))
    if edition_rows:
        latest = edition_rows[-1][0]
        names = author_text(ordered_authors(latest_facts), identifiers=False)
        abstract = latest_facts.get('abstract', '')
        latest_citation = json.loads((output / 'editions' / latest / 'CITATION.json').read_text('utf-8'))
        body = ''
        if abstract:
            body += '<p class="guavajam-abstract">' + escape(abstract) + '</p>'
        body += ('<p class="guavajam-actions"><a href="editions/' + latest +
                 '/read/index.html">Read</a><a href="editions/' + latest +
                 '/read/documents.html">Documents</a><a href="browse/index.html">Search</a></p>' +
                 '<section class="guavajam-edition-section"><h2>Latest edition</h2>')
        sha, ed_label, day, _ = edition_rows[-1]
        body += ('<div class="guavajam-latest-edition"><h3><a href="editions/' + sha +
                 '/read/index.html">' + escape(ed_label) + '</a>' +
                 detail_link('editions/' + sha + '/index.html', ed_label + ' record') +
                 '</h3><p class="guavajam-meta"><time datetime="' + escape(day, quote=True) +
                 '">' + escape(day) + '</time></p></div>')
        if len(edition_rows) > 1:
            body += '<p><a href="editions/index.html">Previous editions</a></p>'
        body += '</section>'
    else:
        latest_citation = None
        latest = None
        body = '<p>No signed editions have been published on the selected history.</p>'
    body += ('<details class="guavajam-provenance"><summary>Publication provenance</summary>' +
             '<p><a href="CATALOG.json">Verified Git history</a></p>' +
             ('<p><a href="' + escape(repository, quote=True) + '">Source repository</a></p>'
              if repository else '') + '</details>')
    (output / 'index.html').write_text(
        page(latest_title, body, ancestors=work_crumbs, citation_record=latest_citation,
             citation_href=('editions/' + latest + '/CITATION.json') if latest else None,
             citation_title=latest_title, citation_authors=names if latest else '',
             citation_published=edition_rows[-1][2] if latest else '',
             citation_edition=len(edition_rows) if latest else None),
        encoding='utf-8')
    (output / 'CATALOG.json').write_bytes(encoded(catalog))
    database = build_work_index(output, catalog, repository=repository, search_assets=search_assets)
    from .directory_pages import build_directories
    build_directories(output, edition_rows, collection=in_collection,
                      search_assets=search_assets, database=database)
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
