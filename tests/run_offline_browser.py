"""Exercise verified offline bytes and generated publisher pages in real browsers."""
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'tests')]
from edition_package import edition_directory
from verify_edition import verify_edition
from test_asset_downloads import DownloadPublicationTests
from test_preview_site import PreviewTests
from test_lantern_gfm import LanternPublicationTests
from test_supporting_metadata import MetadataPublicationTests
from issuance import authorization
from preview_site import preview_server
from public_record import EDITION_RECORDS, WORK_RECORDS, write_records


@contextmanager
def browser_fixture():
    fixture=DownloadPublicationTests('test_mixed_inline_external_and_supporting_references_build_and_verify_offline')
    try:
        fixture.setUp();fixture.source()
        manuscript=fixture.repo/'works/sample/manuscript.md'
        with manuscript.open('a') as stream:
            stream.write('\n<a id="sec-2"></a>\n## Comparison of $x=x$\n\n'
                         'The navigation reuses the reviewed section title and anchor.\n\n'
                         '<a id="detail-2"></a>\n### Theorem 2 — Detail\n\n'
                         'This deeper heading stays outside the major-section outline.\n\n'
                         '<a id="sec-3"></a>\n## Reading the evidence\n\n'
                         'The supporting records follow the manuscript.[^reader-note]\n\n'
                         '[^reader-note]: A footnote keeps its own fragment and backlink.\n')
        fixture.git('add','works');fixture.git('commit','-qm','Review navigable offline edition')
        fixture.commit=fixture.git('rev-parse','HEAD')
        manifest=fixture.build_downloads()
        saved=fixture.base/'complete.zip'
        shutil.copyfile(fixture.output/manifest['supplementary_package']['filename'],saved)
        shutil.rmtree(fixture.output)
        with edition_directory(saved) as directory:
            verify_edition(directory)
            yield directory
    finally:fixture.doCleanups()


@contextmanager
def site_fixture():
    fixture = PreviewTests()
    try:
        fixture.setUp()
        source = fixture.repo / 'works/sample-work/manuscript.md'
        # Keep the actual starter; add only the wide content needed to prove panning.
        headers = [f'Column{n}WithAnUnbrokenLabel' for n in range(8)]
        wide = ('\n<a id="keyboard-overflow"></a>\n## Keyboard overflow\n\n'
                '| ' + ' | '.join(headers) + ' |\n'
                '| ' + ' | '.join(['---'] * 8) + ' |\n'
                '| ' + ' | '.join(['entry'] * 8) + ' |\n\n'
                ': Table 2 — Keyboard scrolling sample\n\n'
                '$$\n' + ' + '.join(f'x_{{{n}}}' for n in range(40)) + '\n$$\n\n'
                '```\nLEFT_' + 'literal_' * 50 + '_RIGHT\n```\n')
        source.write_text(source.read_text() + wide)
        fixture.git('add', 'works')
        fixture.git('commit', '-qm', 'Review keyboard overflow examples')
        fixture.commit = fixture.git('rev-parse', 'HEAD')
        metadata = fixture.assemble()
        package = fixture.preview / 'package'
        manifest = verify_edition(package)
        receipt = {**authorization(package, manifest), 'issuing': True,
                   'published_at': fixture.timestamp}
        slug, version = fixture.tag.split('/')[1:]
        registration = fixture.repo / 'registrations/editions' / slug / (version + '.json')
        registration.parent.mkdir(parents=True)
        registration.write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n')
        fixture.git('add', 'registrations')
        notices = fixture.repo / 'notices' / slug / version
        states = []
        with ExitStack() as stack:
            for state, notice in (
                ('published', None),
                ('corrected', '2026-11-07 Correction: The notation example remains illustrative.\n'),
                ('withdrawn', '2026-11-08 Withdrawal: Retained only as an archived example.\n'),
            ):
                if notice:
                    notices.mkdir(parents=True, exist_ok=True)
                    (notices / ('0001.md' if state == 'corrected' else '0002.md')).write_text(notice)
                    fixture.git('add', 'notices')
                write_records(fixture.repo, fixture.tag, package=package)
                directory = shutil.copytree(fixture.preview, fixture.base / state)
                # Serve exact publisher output, without the preview-only banner.
                for route, generated in (
                    (f'/works/{slug}', WORK_RECORDS / slug / 'index.html'),
                    ('/' + fixture.tag, EDITION_RECORDS / slug / version / 'index.html'),
                ):
                    shutil.copyfile(fixture.repo / generated, directory / metadata['routes'][route]['file'])
                server = stack.enter_context(preview_server(directory, metadata, 0))
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                def stop(server=server, thread=thread):
                    server.shutdown()
                    thread.join(timeout=5)
                stack.callback(stop)
                states.append({'state': state, 'base': f'http://127.0.0.1:{server.server_address[1]}',
                               'work': f'/works/{slug}', 'record': '/' + fixture.tag,
                               'recordURL': manifest['record_url'], 'htmlSHA256': manifest['html_sha256']})
            yield states
    finally:
        fixture.doCleanups()


@contextmanager
def lantern_fixture():
    fixture = LanternPublicationTests('test_both_branches_publish_and_verify_with_exact_reviewed_sources')
    try:
        fixture.setUp()
        fixture.source()
        fixture.build()
        verify_edition(fixture.output)
        yield fixture.output
    finally:
        fixture.doCleanups()


@contextmanager
def metadata_fixture():
    fixture = MetadataPublicationTests('test_fields_are_document_scoped_escaped_and_never_become_edition_metadata')
    try:
        fixture.setUp(); fixture.source(); fixture.build()
        verify_edition(fixture.output)
        yield fixture.output
    finally:
        fixture.doCleanups()


if __name__=='__main__':
    with browser_fixture() as directory, site_fixture() as sites:
        result = subprocess.run(['node','--test','tests/test_offline_browser.mjs',
            'tests/test_reader_site.mjs'], cwd=ROOT, env={**os.environ,
            'PRESS_OFFLINE_TEST_DIR':str(directory), 'PRESS_SITE_TESTS':json.dumps(sites)}).returncode
        if result:
            raise SystemExit(result)
    with lantern_fixture() as directory, metadata_fixture() as metadata:
        raise SystemExit(subprocess.run(['node', '--test', 'tests/test_lantern_browser.mjs', 'tests/test_speaker_browser.mjs', 'tests/test_presentation_regression.mjs', 'tests/test_supporting_metadata_browser.mjs'], cwd=ROOT,
            env={**os.environ, 'PRESS_LANTERN_TEST_DIR':str(directory), 'PRESS_METADATA_TEST_DIR':str(metadata)}).returncode)
