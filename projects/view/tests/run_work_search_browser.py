"""Qualify paged work discovery from real signed history, without a server API."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO_ROOT / 'tests/support'),
               str(REPO_ROOT / 'projects/core/src'), str(REPO_ROOT / 'projects/view/src')]
from guavajam_test_support.discovery_fixture import Publisher
from guavajam_core.chat_gfm import review_gfm
from guavajam.search_index import emit_browser_assets
from guavajam.site import build_site


def prepare(directory):
    directory = Path(directory)
    directory.mkdir(parents=True)
    site = directory / 'site'
    site.mkdir()
    runtime = REPO_ROOT / 'node_modules/@sqlite.org/sqlite-wasm/dist'
    if 'GUAVAJAM_SQLITE_WASM_DIR' not in os.environ and runtime.is_dir():
        os.environ['GUAVAJAM_SQLITE_WASM_DIR'] = str(runtime)
    if not emit_browser_assets(site):
        raise RuntimeError('Install the pinned SQLite WASM runtime with npm ci before this qualification')
    publisher = Publisher(directory / 'publisher')
    support = publisher.repo / 'content/supporting'
    support.mkdir()
    older = None
    for number in range(1, 23):
        body = ('Palaeotidal vanished evidence.' if number == 1 else f'Published observation {number}.')
        publisher.source('Archive work', body)
        manuscript = publisher.repo / 'content/manuscript.md'
        manuscript.write_text(manuscript.read_text().replace('rights: CC-BY-4.0\n',
                              'rights: CC-BY-4.0\nkeywords:\n  - Café studies\n'))
        if number == 1:
            (support / 'archived.md').write_text(
                '<a name="archived-document"></a>\n\n# Archived note\n\n'
                '<a name="archived-evidence"></a>\n\nOlder documentary evidence.\n')
        elif number == 2:
            (support / 'archived.md').unlink()
        if number == 22:
            with manuscript.open('a') as stream:
                for section in range(1, 45):
                    phrase = 'Bathymetric eco**logy**.' if section == 24 else f'Indexed evidence {section}.'
                    if section == 44:
                        phrase = 'Bathyal citational evidence for the last passage.'
                    stream.write(f'\n<a id="section-{section}"></a>\n## Section {section:02d}\n\n{phrase}\n')
            for appendix in range(1, 24):
                (support / f'unlinked-{appendix:02d}.md').write_text(
                    '<a name="document"></a>\n\n' + f'# Appendix {appendix:02d}\n\n' +
                    '<a name="evidence"></a>\n\n' + f'Unlinked supporting evidence {appendix}.' +
                    (' Abyssalappendixselector.' if appendix == 23 else '') + '\n')
            with (support / 'unlinked-23.md').open('a') as stream:
                for passage in range(1, 45):
                    phrase = 'Bathyal supporting citation.' if passage == 44 else f'Supporting proof {passage}.'
                    stream.write(f'\n<a name="support-{passage:02d}"></a>\n\n{phrase}\n')
            reviewed, _ = review_gfm({'title': 'Reviewed correspondence', 'scope': 'Synthetic review', 'passages': [
                {'id': 'm-author', 'label': 'Author', 'speaker': 'author',
                 'text': '# Author analysis\n\nAuthor observations.'},
                {'id': 'm-reply', 'label': 'Correspondent', 'speaker': 'responder',
                 'text': '# Quoted analysis\n\nQuotientwave evidence.'},
            ]})
            (support / 'correspondence.md').write_bytes(reviewed)
        date = '2026-10-01T12:00:00Z' if number == 22 else f'2026-11-{number:02d}T12:00:00Z'
        message = f'Observation {number:02d}'
        if number == 1:
            message += '\n\nShorelinechronicle records the historical review.\nRechecked provenance.'
        revision = publisher.publish(message, date=date)
        if number == 1:
            older = revision
    relative = 'sources/work'
    build_site(publisher.repo, site / relative, repository='https://example.org/work.git',
               search_assets='../../', **publisher.trust)
    unpublished = 'sources/unpublished/'
    infrastructure = publisher.git('rev-list', '--max-parents=0', 'HEAD')
    build_site(publisher.repo, site / unpublished, ref=infrastructure,
               repository='https://example.org/unpublished.git', search_assets='../../', **publisher.trust)
    description = {'root': str(site), 'work': relative + '/', 'latest': revision, 'older': older,
                   'editions': 23, 'documents': 25, 'manuscriptPassages': 45, 'unpublished': unpublished,
                   'supportingPassages': 46,
                   'historical': relative + '/editions/' + older + '/read/index.html',
                   'section': relative + '/editions/' + revision + '/read/index.html#section-24',
                   'lastSection': relative + '/editions/' + revision + '/read/index.html#section-44'}
    target = directory / 'work-search-browser.json'
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
    with tempfile.TemporaryDirectory(prefix='guavajam-work-search-browser-') as directory:
        fixture = prepare(Path(directory) / 'fixture')
        output = args.output or Path(directory) / 'evidence'
        return subprocess.run(['node', str(Path(__file__).with_name('test_work_search_browser.mjs')),
                               str(fixture), str(output)], check=False).returncode


if __name__ == '__main__':
    sys.exit(main())
