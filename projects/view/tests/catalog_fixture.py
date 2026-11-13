"""A different publisher's local Git repository; signing keys never enter the site."""
from pathlib import Path
import json
import os
import subprocess


def command(root, *args, env=None):
    result = subprocess.run(args, cwd=root, env=env, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def make_catalog_fixture(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    repo = directory / 'source'
    repo.mkdir()
    for args in [('init', '-q', '-b', 'main'), ('config', 'user.name', 'Fixture Publisher'),
                 ('config', 'user.email', 'fixture@example.org')]:
        command(repo, 'git', *args)
    home = directory / 'gpg'
    home.mkdir(mode=0o700)
    env = {**os.environ, 'GNUPGHOME': str(home)}
    command(repo, 'gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase', '',
            '--quick-generate-key', 'Catalog Fixture <fixture@example.org>', 'ed25519', 'sign', '0', env=env)
    listing = command(repo, 'gpg', '--batch', '--with-colons', '--list-secret-keys', env=env)
    fingerprint = next(row.split(':')[9] for row in listing.splitlines() if row.startswith('fpr:'))
    command(repo, 'git', 'config', 'gpg.format', 'openpgp')
    command(repo, 'git', 'config', 'user.signingkey', fingerprint)
    revisions = []
    for slug, title, phrase in [('field-notes', 'Field Notes', 'First observation.'),
                                ('field-notes', 'Field Notes, Revised', 'Revised observation.'),
                                ('other-work', 'Another Work', 'An independent text.')]:
        work = repo / 'works' / slug
        (work / 'supporting').mkdir(parents=True, exist_ok=True)
        (work / 'manuscript.md').write_text('''---
schema: press.work.v1
title: "TITLE"
author: "Example & Colleague"
slug: SLUG
language: en
status: draft
rights: CC-BY-4.0
abstract: "A reviewed abstract with literal <markup> & Unicode: Élan."
schema_type: ScholarlyArticle
---

# TITLE

<a id="opening"></a>
## Opening

PHRASE See [the supporting claim](supporting/note.md#claim).

<a id="guavajam-catalog-main"></a>
## A deliberate viewer identifier collision

The source anchor remains unchanged.
'''.replace('TITLE', title).replace('SLUG', slug).replace('PHRASE', phrase))
        (work / 'supporting/note.md').write_text('''<a name="document"></a>

# A supporting note

<a name="claim"></a>

The exact, independently citable claim.
''')
        command(repo, 'git', 'add', '-A')
        command(repo, 'git', 'commit', '-qm', 'Review ' + title)
        command(repo, 'git', 'commit', '--allow-empty', '-S', '-m', 'Publish ' + title,
                '-m', 'Press-Publication: 1\nPress-Work: works/' + slug + '/manuscript.md\nPress-Published: ' +
                ['2026-10-01', '2026-11-01', '2026-11-03'][len(revisions)], env=env)
        revisions.append(command(repo, 'git', 'rev-parse', 'HEAD'))
    catalog = {
        'schema': 'guavajam.catalog.v1', 'title': 'Collected Publications',
        'publisher': 'Independent Example Imprint', 'language': 'en',
        'description': 'A reproducible fixture, not a live DOI registration.',
        'base_url': 'https://publisher.example/collection',
        'source_view': 'https://code.example/repository/blob/{revision}/{path}',
        'works': [
            {'slug': 'field-notes', 'concept_doi': '10.1234/field-notes',
             'metadata_revision': revisions[1], 'recommended_revision': revisions[1],
             'editions': [
                 {'revision': revisions[0], 'published': '2026-10-01', 'label': 'First edition',
                  'status': 'available', 'doi': '10.1234/field-notes.v1'},
                 {'revision': revisions[1], 'published': '2026-11-01', 'label': 'Revised edition',
                  'status': 'available', 'doi': '10.1234/field-notes.v2'}]},
            {'slug': 'other-work', 'metadata_revision': revisions[2],
             'editions': [{'revision': revisions[2], 'published': '2026-11-03',
                           'status': 'unavailable',
                           'notices': [{'date': '2026-11-04', 'text': 'Reading distribution is unavailable; the record remains.'}]}]}
        ]}
    catalog_path = directory / 'catalog.json'
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n')
    return {'repo': repo, 'catalog': catalog, 'catalog_path': catalog_path,
            'fingerprint': fingerprint, 'gpg_home': home, 'revisions': revisions}
