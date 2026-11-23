"""Build real signed repositories and qualify aggregate SQLite search in browsers."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO_ROOT / 'tests/support'),
               str(REPO_ROOT / 'src'), str(REPO_ROOT / 'some-implementations/a-guavajam-static-website-generator/src')]
from guavajam_test_support.discovery_fixture import Publisher
from a_guavajam_static_website_generator.search_index import create_index, emit_browser_assets, homepage
from a_guavajam_static_website_generator.site import build_site


def metadata(publisher, title, body, *, keyword, subject=None):
    publisher.source(title, body)
    source = publisher.repo / 'content/manuscript.md'
    extra = 'keywords:\n  - ' + keyword + '\n'
    if subject:
        extra += ('subjects:\n  - subject: Java\n    subject_scheme: ' + subject +
                  '\n    language: en\n')
    source.write_text(source.read_text().replace('rights: CC-BY-4.0\n', 'rights: CC-BY-4.0\n' + extra))


def prepare(directory):
    directory = Path(directory)
    directory.mkdir(parents=True)
    site = directory / 'site'
    site.mkdir()
    entries = []
    old_revision = None
    for number in range(1, 23):
        name = f'work-{number:02d}'
        publisher = Publisher(directory / name)
        if number == 1:
            metadata(publisher, 'Old geography', 'Palaeoriver is a retired observation.',
                     keyword='Retired topic', subject='Geography')
            old_revision = publisher.publish('Historical observation', date='2026-10-01T12:00:00Z')
        if number == 2:
            supporting = publisher.repo / 'content/supporting'
            supporting.mkdir()
            (supporting / 'evidence.md').write_text(
                '---\ntitle: Evidence\n---\n\n'
                '<a name="claim"></a>\n\nGlaciolacustrine supporting evidence.\n')
        body = 'A collection observation.'
        if number == 2:
            body += ' See [the evidence](supporting/evidence.md#claim).'
        subject = {1: 'Geography', 2: 'Computing'}.get(number)
        metadata(publisher, f'Work {number:02d}', body,
                 keyword='Café studies' if number == 1 else 'Field observations', subject=subject)
        if number == 3:
            source = publisher.repo / 'content/manuscript.md'
            source.write_text(source.read_text().replace('<a id="opening"></a>\n## Opening\n\n', '') +
                              '\nFreeprosebodyevidence stays findable without a declared source anchor.\n')
        revision = publisher.publish(f'Publish work {number:02d}', date=f'2026-11-{number:02d}T12:00:00Z')
        relative = 'sources/' + name
        entry = {'repository': f'https://example.org/{name}.git', 'path': relative, 'tip': revision}
        build_site(publisher.repo, site / relative, repository=entry['repository'],
                   search_assets='../../', **publisher.trust)
        entries.append(entry)
    create_index(site, entries)
    runtime = REPO_ROOT / 'node_modules/@sqlite.org/sqlite-wasm/dist'
    if 'GUAVAJAM_SQLITE_WASM_DIR' not in os.environ and runtime.is_dir():
        os.environ['GUAVAJAM_SQLITE_WASM_DIR'] = str(runtime)
    if not emit_browser_assets(site):
        raise RuntimeError('Install the pinned SQLite WASM runtime with npm ci before this qualification')
    (site / 'index.html').write_text(homepage(site, interactive=True))
    description = {'root': str(site), 'works': len(entries), 'historical':
                   'sources/work-01/editions/' + old_revision + '/read/index.html'}
    target = directory / 'search-browser.json'
    target.write_text(json.dumps(description))
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.prepare:
        print(prepare(args.prepare))
        return 0
    with tempfile.TemporaryDirectory(prefix='guavajam-search-browser-') as directory:
        fixture = prepare(Path(directory) / 'fixture')
        output = args.output or Path(directory) / 'evidence'
        return subprocess.run(['node', str(Path(__file__).with_name('test_search_browser.mjs')),
                               str(fixture), str(output)], check=False).returncode


if __name__ == '__main__':
    sys.exit(main())
