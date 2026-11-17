"""Build a static publication catalog, DOI records and optional reading views.

The source repository is read-only. This command never signs a publication,
registers a DOI, downloads media, deploys files or decides signer authority.
"""
from __future__ import annotations

import argparse
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import shutil
import sys
import tempfile
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from guavajam_core.catalog import CatalogError, compare, encoded, read_catalog, source_url, READABLE_STATUSES
from .renderer import RenderError, git_blob, render
from .site_views import (absolute, abstract, authors, canonical_record, citation,
                         citation_data, citation_filename, citation_page,
                         decorate_reading, doi_display, e, edition_root,
                         edition_advisory, metadata, page, relative, source_filename,
                         source_identity, status)

MAX_SITE_BYTES = 512 * 1024 * 1024
MAX_SITE_FILES = 20000


class Output:
    def __init__(self, directory):
        self.directory = directory
        self.files = {}
        self.bytes = 0

    def put(self, name, content):
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or name in self.files or str(path) != name:
            raise CatalogError('duplicate or invalid generated destination')
        raw = content.encode('utf-8') if isinstance(content, str) else content
        if len(self.files) >= MAX_SITE_FILES or self.bytes + len(raw) > MAX_SITE_BYTES:
            raise CatalogError('derived site exceeds 20,000 files or 512 MiB; split the catalog')
        target = self.directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        self.files[name] = hashlib.sha256(raw).hexdigest()
        self.bytes += len(raw)


def _ordered(work):
    return sorted(work['editions'], key=lambda item: (item['published'], item['revision']), reverse=True)


def _edition_label(edition):
    return edition.get('label', 'Revision ' + edition['revision'][:12])


def _edition_link(catalog, filename, work, edition, facts, *, provisional=False):
    root = edition_root(work['slug'], edition['revision'])
    content = ('<h3><a href="' + e(relative(filename, root + '/index.html')) + '">' +
               e(_edition_label(edition)) + '</a></h3><p>' + e(facts['title']) + '</p>' +
               '<p><time datetime="' + e(edition['published']) + '">' + e(edition['published']) +
               '</time> · ' + status(edition) +
               ('<span class="guavajam-preview-accessible"> Unsigned, unissued edition.</span>'
                if provisional else '') + '</p>')
    if edition.get('doi'):
        content += doi_display(edition['doi'], 'Edition identifier')
    content += '<p class="guavajam-administration">Git SHA-1 <code>' + e(edition['revision']) + '</code></p>'
    return '<li' + (' class="guavajam-preview-change"' if provisional else '') + '><article>' + content + '</article></li>'


def _registry(catalog, publications, *, provisional=()):
    filename = 'index.html'
    body = '<h1>' + e(catalog['title']) + '</h1><p class="guavajam-edition-identity">' + e(catalog['publisher']) + '</p>'
    if catalog.get('description'):
        body += '<p>' + e(catalog['description']) + '</p>'
    body += '<p>Browse works, their DOI records and ' + ('source' if provisional else 'signed source') + ' editions. Reading views are optional presentations of those sources.</p>'
    if not catalog['works']:
        body += '<p>No publications have been listed in this catalog yet.</p>'
    body += '<ol class="guavajam-works">'
    for work in catalog['works']:
        facts = publications[work['slug'], work['metadata_revision']]['facts']
        changed = any((work['slug'], edition['revision']) in provisional for edition in work['editions'])
        body += '<li' + (' class="guavajam-preview-change"' if changed else '') + '><article><h2><a href="' + e('works/' + work['slug'] + '/index.html') + '">' + e(facts['title']) + '</a></h2>'
        body += authors(facts) + abstract(facts)
        if work.get('concept_doi'):
            body += doi_display(work['concept_doi'], 'Work identifier')
        else:
            body += '<p class="guavajam-administration">No work DOI is listed.</p>'
        count = len(work['editions'])
        # The scoped green rail identifies a changed work. Don't repeat that
        # announcement in the visible edition count; keep it accessible to
        # nonvisual readers without changing the signed-catalog markup.
        kind = (' edition' if changed else ' signed source edition') + ('s' if count != 1 else '')
        accessible = ('<span class="guavajam-preview-accessible"> This work contains an unsigned proposed edition.</span>'
                      if changed else '')
        body += '<p>' + str(count) + kind + accessible + '</p></article></li>'
    body += '</ol>'
    if catalog['works']:
        body += '<p><a href="feed.xml">Publication updates (Atom)</a></p>'
    return page(catalog, filename, catalog['title'], body)


def _work_page(catalog, work, publications, *, provisional=()):
    filename = 'works/' + work['slug'] + '/index.html'
    basis = publications[work['slug'], work['metadata_revision']]
    facts = basis['facts']
    metadata_provisional = (work['slug'], work['metadata_revision']) in provisional
    body = '<h1' + (' class="guavajam-preview-change"' if metadata_provisional else '') + ' lang="' + e(facts['language']) + '">' + e(facts['title']) + '</h1>' + authors(facts) + abstract(facts)
    if work.get('concept_doi'):
        body += doi_display(work['concept_doi'], 'Work identifier')
    else:
        body += '<p>No work DOI is listed.</p>'
    body += '<p>This page describes the work across editions. For a quotation or reproducible claim, select an exact edition and committed passage.</p>'
    selected = work.get('recommended_revision')
    if selected:
        root = edition_root(work['slug'], selected)
        proposed_recommendation = (work['slug'], selected) in provisional
        body += ('<p class="guavajam-actions' + (' guavajam-preview-change' if proposed_recommendation else '') + '"><a href="' + e(relative(filename, root + '/read/index.html')) + '">Read the recommended edition</a>'
                 '<a href="' + e(relative(filename, root + '/index.html')) + '">Recommended edition record</a></p>')
    else:
        body += '<p>No edition is currently recommended by this catalog.</p>'
    basis_link = relative(filename, edition_root(work['slug'], work['metadata_revision']) + '/index.html')
    body += ('<p class="guavajam-administration">The displayed title, authors and abstract come from '
             '<a href="' + e(basis_link) + '">' + ('Git revision' if metadata_provisional else 'signed revision') +
             ' <code>' + e(work['metadata_revision']) + '</code>' +
             ('<span class="guavajam-preview-accessible"> (unsigned draft)</span>' if metadata_provisional else '') +
             '</a>.</p>')
    body += '<h2>Editions</h2><ol class="guavajam-editions">'
    for edition in _ordered(work):
        body += _edition_link(catalog, filename, work, edition, publications[work['slug'], edition['revision']]['facts'],
                              provisional=(work['slug'], edition['revision']) in provisional)
    body += '</ol>'
    return page(catalog, filename, facts['title'], body, work=work, work_title=facts['title'],
                head=metadata(facts, catalog['publisher'], identifier=work.get('concept_doi')))


def _edition_page(catalog, work, edition, publication, *, provisional=False):
    root = edition_root(work['slug'], edition['revision'])
    filename = root + '/index.html'
    facts = publication['facts']
    canonical = publication['canonical']
    body = '<h1 lang="' + e(facts['language']) + '">' + e(facts['title']) + '</h1>' + authors(facts)
    body += '<p>' + e(_edition_label(edition)) + ' · <time datetime="' + e(edition['published']) + '">' + e(edition['published']) + '</time> · ' + status(edition) + '</p>'
    if edition.get('doi'):
        body += doi_display(edition['doi'], 'Edition identifier')
    else:
        body += ('<p>No edition DOI is listed. This committed source is not an issued publication.</p>'
                 if provisional else '<p>No edition DOI is listed. The signed source identity is sufficient to identify this edition.</p>')
    if work.get('concept_doi'):
        body += doi_display(work['concept_doi'], 'Work across editions')
    body += abstract(facts) + edition_advisory(catalog, filename, work, edition)
    if edition['status'] in READABLE_STATUSES:
        body += ('<p class="guavajam-actions"><a href="read/index.html">Read this edition</a>'
                 '<a href="read/cite.html">Cite committed passages</a><a download href="read/source.gfm">Download exact GFM source</a></p>')
        supporting = [(path, doc) for path, doc in publication['documents'].items()
                      if path != canonical['manuscript']]
        if supporting:
            body += '<h2>Supporting source documents</h2><ul>'
            for path, doc in sorted(supporting):
                body += '<li><a href="' + e('read/' + doc['view']) + '">' + e(doc['title']) + '</a></li>'
            body += '</ul>'
        other = source_url(catalog, edition['revision'], canonical['manuscript'])
        if other:
            body += '<p><a href="' + e(other) + '">Open the pinned source in another viewer</a></p>'
    else:
        body += ('<p class="guavajam-source-guide">This is a retained bibliographic record. The unavailable edition has no distributed reading view or source download in this build. Its signed source identity is unchanged.</p>')
    body += '<h2>Cite the committed source</h2>' + citation(catalog, facts, edition, canonical)
    body += '<p><a download href="citation.json">Download citation data</a></p><p class="guavajam-administration">The page URL locates a presentation; it is not the canonical source identifier.</p>'
    body += '<p>Rights: ' + e(facts['rights']) + '</p>'
    return page(catalog, filename, facts['title'] + ' — ' + _edition_label(edition), body,
                head=metadata(facts, catalog['publisher'], identifier=edition.get('doi'),
                              published=edition['published'], canonical=canonical),
                work=work, edition=edition, work_title=facts['title'], provisional=provisional)


def _discovery(catalog, publications, pages):
    """No wall clock or inferred publication time; dates are reviewed catalog facts."""
    sitemap = ET.Element('urlset', xmlns='http://www.sitemaps.org/schemas/sitemap/0.9')
    for filename in sorted(pages):
        url = ET.SubElement(sitemap, 'url')
        ET.SubElement(url, 'loc').text = absolute(catalog, filename)
    result = {'sitemap.xml': ET.tostring(sitemap, encoding='utf-8', xml_declaration=True)}
    if not catalog['works']:
        return result
    entries = [(work, edition) for work in catalog['works'] for edition in work['editions']]
    def update(item):
        edition = item[1]
        return max([edition['published'], *(n['date'] for n in edition.get('notices', []))])
    feed = ET.Element('feed', xmlns='http://www.w3.org/2005/Atom')
    ET.SubElement(feed, 'id').text = catalog['base_url'] + '/'
    ET.SubElement(feed, 'title').text = catalog['title']
    ET.SubElement(feed, 'updated').text = max(map(update, entries)) + 'T00:00:00Z'
    ET.SubElement(feed, 'link', href=catalog['base_url'] + '/feed.xml', rel='self')
    ET.SubElement(feed, 'link', href=catalog['base_url'] + '/')
    for work, edition in sorted(entries, key=lambda item: (update(item), item[1]['revision']), reverse=True):
        facts = publications[work['slug'], edition['revision']]['facts']
        url = absolute(catalog, edition_root(work['slug'], edition['revision']) + '/index.html')
        entry = ET.SubElement(feed, 'entry')
        ET.SubElement(entry, 'id').text = url
        ET.SubElement(entry, 'title').text = facts['title'] + ' — ' + _edition_label(edition)
        ET.SubElement(entry, 'link', href=url)
        ET.SubElement(entry, 'published').text = edition['published'] + 'T00:00:00Z'
        ET.SubElement(entry, 'updated').text = update((work, edition)) + 'T00:00:00Z'
        from guavajam_core.bibliography import ordered_authors
        for author in ordered_authors(facts):
            ET.SubElement(ET.SubElement(entry, 'author'), 'name').text = author['name']
        summary = edition['status'].capitalize() + '. ' + source_identity(publications[work['slug'], edition['revision']]['canonical'])
        if edition.get('notices'):
            summary += '. ' + edition['notices'][-1]['text']
        ET.SubElement(entry, 'summary').text = summary
    result['feed.xml'] = ET.tostring(feed, encoding='utf-8', xml_declaration=True)
    return result


def check_links(output):
    """Reject broken static navigation, including citations into source fragments."""
    pages = {}
    for name in output.files:
        if not name.endswith('.html'):
            continue
        ids, links = [], []
        class Links(HTMLParser):
            def handle_starttag(self, tag, attrs):
                values = dict(attrs)
                if 'id' in values:
                    ids.append(values['id'])
                if tag in {'a', 'link'} and 'href' in values:
                    links.append(values['href'])
        parser = Links()
        parser.feed((output.directory / name).read_text('utf-8'))
        parser.close()
        if len(ids) != len(set(ids)):
            raise CatalogError('duplicate generated page identity: ' + name)
        pages[name] = (set(ids), links)
    for name, (_, links) in pages.items():
        for href in links:
            target = urlsplit(href)
            if target.scheme or target.netloc:
                continue
            destination = posixpath.normpath(posixpath.join(posixpath.dirname(name), unquote(target.path))) if target.path else name
            if destination not in output.files:
                raise CatalogError('unresolved generated link: ' + name + ' -> ' + href)
            if target.fragment and (destination not in pages or unquote(target.fragment) not in pages[destination][0]):
                raise CatalogError('unresolved generated fragment: ' + name + ' -> ' + href)


def build_site(repo, catalog_path, output, *, fingerprints=(), allowed_signers=None,
               previous_catalog=None, initial_catalog=False, view_cache=None,
               preview_candidates=None):
    if bool(previous_catalog) == bool(initial_catalog):
        raise CatalogError('provide the last deployed catalog or explicitly select the initial catalog build')
    if fingerprints and allowed_signers:
        raise CatalogError('provide exactly one external GPG fingerprint policy or SSH allowed-signers file')
    candidates = set()
    if preview_candidates is not None:
        if not isinstance(preview_candidates, list) or not 1 <= len(preview_candidates) <= 256:
            raise CatalogError('invalid isolated preview candidates')
        for item in preview_candidates:
            if (not isinstance(item, dict) or set(item) != {'work', 'revision'}
                    or not isinstance(item['work'], str) or not isinstance(item['revision'], str)):
                raise CatalogError('invalid isolated preview candidate')
            candidates.add((item['work'], item['revision']))
        if len(candidates) != len(preview_candidates):
            raise CatalogError('duplicate isolated preview candidate')
    catalog = read_catalog(catalog_path, provisional=candidates)
    editions = {(work['slug'], edition['revision']) for work in catalog['works'] for edition in work['editions']}
    if not candidates <= editions:
        raise CatalogError('preview candidate must select a catalog edition')
    if not (fingerprints or allowed_signers) and (not candidates or editions != candidates):
        raise CatalogError('provide external signer trust for every non-provisional edition')
    if 'base_url' not in catalog:
        raise CatalogError('HTML site generation requires an explicit base_url in repository configuration')
    if previous_catalog:
        compare(read_catalog(previous_catalog), catalog, provisional=candidates)
    repo = Path(repo).resolve(strict=True)
    output = Path(output).absolute()
    if output.is_symlink() or output.resolve().is_relative_to(repo):
        raise CatalogError('site output must be outside the read-only source repository and not a symlink')
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise CatalogError('site destination must be new or empty; deploy a complete tree, never an overlay')
    output.parent.mkdir(parents=True, exist_ok=True)
    # Build and audit privately. An error leaves the chosen destination untouched.
    with tempfile.TemporaryDirectory(prefix='.guavajam-site-', dir=output.parent) as temporary:
        temporary = Path(temporary)
        staging = temporary / 'site'
        staging.mkdir()
        writer = Output(staging)
        publications, indexable = {}, {'index.html'}
        for work in catalog['works']:
            for edition in _ordered(work):
                sha = edition['revision']
                render_dir = temporary / 'view'
                provisional = (work['slug'], sha) in candidates
                if provisional:
                    render(repo, sha, work['slug'], render_dir, preview=True)
                elif view_cache is None:
                    render(repo, sha, work['slug'], render_dir, fingerprints=fingerprints, allowed_signers=allowed_signers)
                else:
                    from .view_cache import cached_render
                    cached_render(view_cache, repo, sha, work['slug'], render_dir,
                                  fingerprints=fingerprints, allowed_signers=allowed_signers)
                view = json.loads((render_dir / 'VIEW.json').read_text('utf-8'))
                if view['preview'] != provisional or view['canonical']['revision'] != sha:
                    raise CatalogError('site edition verification disagrees with its selected authority')
                canonical = canonical_record(sha, view['canonical']['manuscript'])
                facts = view['facts']
                concept = facts.get('identifiers', {}).get('concept_doi')
                if concept and work.get('concept_doi') != concept:
                    raise CatalogError('catalog work DOI conflicts with the signed source declaration')
                publication = {'canonical': canonical, 'facts': facts, 'documents': view['documents']}
                publications[work['slug'], sha] = publication
                root = edition_root(work['slug'], sha)
                writer.put(root + '/index.html', _edition_page(catalog, work, edition, publication, provisional=provisional))
                indexable.add(root + '/index.html')
                manuscript_info = view['documents'][canonical['manuscript']]
                writer.put(root + '/citation.json', encoded(citation_data(catalog, facts, edition, canonical, manuscript_info['anchors'], provisional=provisional)))
                if edition['status'] in READABLE_STATUSES:
                    file_hashes = {}
                    for document_path, document in sorted(view['documents'].items()):
                        filename = root + '/read/' + document['view']
                        selected = canonical_record(sha, canonical['manuscript'], document_path)
                        text = (render_dir / document['view']).read_text('utf-8')
                        # The signed renderer owns source validation. The site layer
                        # adds navigation/citations, never edits publication text.
                        text = decorate_reading(text, catalog, filename, work, edition, document, selected,
                                                work_title=facts['title'], provisional=provisional)
                        writer.put(filename, text)
                        file_hashes[document['view']] = writer.files[filename]
                        indexable.add(filename)
                        cite_name = citation_filename(filename)
                        writer.put(cite_name, citation_page(catalog, cite_name, work, edition, facts, document,
                                                            selected, filename, provisional=provisional))
                        writer.put(str(PurePosixPath(cite_name).with_suffix('.json')),
                                   encoded(citation_data(catalog, facts, edition, selected, document['anchors'], provisional=provisional)))
                        raw = git_blob(repo, sha, document_path)
                        if hashlib.sha256(raw).hexdigest() != document['source_sha256']:
                            raise CatalogError('source download differs from the verified document')
                        writer.put(source_filename(filename), raw)
                    # Optional view manifest describes these decorated views, not
                    # the standalone intermediate files or an independent edition.
                    view['files'] = file_hashes
                    view['catalog_sha256'] = hashlib.sha256(encoded(catalog)).hexdigest()
                    writer.put(root + '/read/VIEW.json', encoded(view))
                shutil.rmtree(render_dir)
        writer.put('index.html', _registry(catalog, publications, provisional=candidates))
        for work in catalog['works']:
            name = 'works/' + work['slug'] + '/index.html'
            writer.put(name, _work_page(catalog, work, publications, provisional=candidates))
            indexable.add(name)
        for name, content in _discovery(catalog, publications, indexable).items():
            writer.put(name, content)
        writer.put('CATALOG.json', encoded(catalog))
        check_links(writer)
        # Manifest is descriptive build evidence, not a new publication authority.
        manifest = {'schema': 'guavajam.site.v1', 'catalog_sha256': writer.files['CATALOG.json'],
                    'canonical_publications': [publications[key]['canonical'] for key in sorted(publications)],
                    'files': dict(sorted(writer.files.items())),
                    'source_signatures_verified': not candidates,
                    'external_media_verified': False,
                    'doi_registration_verified': False}
        writer.put('SITE.json', encoded(manifest))
        # Re-check before installing the complete directory. A concurrently used
        # destination is an error, not permission to replace an existing site.
        if output.is_symlink() or (output.exists() and (not output.is_dir() or any(output.iterdir()))):
            raise CatalogError('site destination changed during build')
        if output.exists():
            output.rmdir()
        os.rename(staging, output)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True, help='read-only local GuavaJam-convention Git repository')
    parser.add_argument('--catalog', type=Path, required=True, help='reviewed administrative JSON catalog')
    parser.add_argument('--output', type=Path, required=True, help='new/empty directory outside the source repository')
    baseline = parser.add_mutually_exclusive_group(required=True)
    baseline.add_argument('--previous-catalog', type=Path, help='independently retained CATALOG.json from the last deployment')
    baseline.add_argument('--initial-catalog', action='store_true', help='explicit first build; never use to bypass an existing DOI binding')
    trust = parser.add_mutually_exclusive_group(required=True)
    trust.add_argument('--trusted-gpg-fingerprint', action='append', default=[])
    trust.add_argument('--ssh-allowed-signers', type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = build_site(args.repo, args.catalog, args.output, fingerprints=args.trusted_gpg_fingerprint,
                              allowed_signers=args.ssh_allowed_signers, previous_catalog=args.previous_catalog,
                              initial_catalog=args.initial_catalog)
    except (ValueError, OSError, UnicodeError) as exc:
        print('GuavaJam site build failed: ' + str(exc), file=sys.stderr)
        return 1
    print('Built', len(manifest['canonical_publications']), 'signed-source edition(s); no publication or DOI was issued.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
