"""Qualify actual extension ZIPs independently, through the public import commands."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
FIXTURE = json.loads((ROOT / 'tests/fixtures/capture/conversation.json').read_text())
LIVE_SHAPE = json.loads((ROOT / 'tests/fixtures/capture/live-shape.json').read_text())
AUDIT_UNAVAILABLE = {'audit-unavailable', 'stalled', 'rate-limit'}
SUCCESS = {'happy', 'cookie-only', 'omission', 'hostile-page', 'multiple-tabs',
           'popup-close', 'live-shape'} | AUDIT_UNAVAILABLE


def command(target, **variables):
    result = subprocess.run(['make', '--no-print-directory', target, f'PYTHON={sys.executable}'],
                            cwd=ROOT, env={**os.environ, **{key: str(value) for key, value in variables.items()}},
                            text=True, capture_output=True)
    if result.returncode:
        raise AssertionError(f'{target} failed: {result.stderr}')
    return result


def verify(directory):
    archives = list(directory.glob('*.zip'))
    assert {archive.stem for archive in archives} == SUCCESS
    for archive in archives:
        fixture = LIVE_SHAPE['native'] if archive.stem == 'live-shape' else FIXTURE
        live = archive.stem == 'live-shape'
        with zipfile.ZipFile(archive) as package:
            assert package.testzip() is None, 'independent CRC and ZIP directory validation'
            expected = {'conversations.json', 'capture-manifest.json'}
            if archive.stem not in AUDIT_UNAVAILABLE:
                expected.add('selected-branch-pages.json')
            assert len(package.infolist()) == len(expected)
            assert set(package.namelist()) == expected
            contents = {name: package.read(name) for name in expected}
            assert all(secret not in data for data in contents.values()
                       for secret in (b'synthetic-only-token', b'synthetic-cookie', b'fixture-account'))
            native_bytes = contents['conversations.json']
            assert json.loads(native_bytes) == [fixture], 'all forks and private context retained without synthesis'
            report = json.loads(contents['capture-manifest.json'])
            assert report['schema'] == 'press-chatgpt-capture-reference/v1'
            assert report['native_graph']['sha256_conversations_json'] == hashlib.sha256(native_bytes).hexdigest()
            assert report['native_graph']['mapping_nodes'] == (9 if live else 8)
            assert report['native_graph']['branch_points'] == 2
            assert report['native_graph']['leaves'] == 3
            audit = report['selected_branch_audit']
            assert audit['status'] == ('unavailable' if archive.stem in AUDIT_UNAVAILABLE else 'audited')
            if audit['status'] == 'audited':
                pages = json.loads(contents['selected-branch-pages.json'])
                assert len(pages) == audit['page_count'] == (1 if live else 2)
                if live:
                    assert pages == LIVE_SHAPE['selected_branch_pages']
                else:
                    assert pages[0]['messages'] == [fixture['mapping']['a2']['message']]
                    assert pages[1]['messages'][0] == fixture['mapping']['u']['message']
                assert pages[-1]['page_info']['has_previous_page'] is False
                assert audit['selected_branch_messages_absent_from_native_mapping'] == (3 if live else int(archive.stem == 'omission'))
                assert audit['pagination_reached_oldest_page'] is True
            assert report['capture_limits']['response_bytes'] <= report['capture_limits']['max_response_bytes']

        listing = command('chat-list', FILE=archive)
        assert json.loads(listing.stdout) == [fixture['conversation_id'], fixture['title']]
        tips = (('original-answer', ['original-prompt', 'original-answer']),
                ('regenerated-answer', ['original-prompt', 'regenerated-answer']),
                ('edited-answer', ['edited-prompt', 'edited-answer'])) if live else (
                ('a1', ['u', 'a1']), ('a2', ['u', 'a2']), ('a3', ['edited-u', 'a3']))
        for tip, selected in tips:
            expected_text = [fixture['mapping'][key]['message']['content']['parts'][0] for key in selected]
            ids = ['m-' + hashlib.sha256(key.encode()).hexdigest() for key in selected]
            markdown = directory / f'{archive.stem}-{tip}.md'
            conversion = command('chat-gfm', FILE=archive, CONVERSATION=fixture['conversation_id'], TIP=tip, OUTPUT=markdown)
            text = markdown.read_text()
            assert expected_text[0] in text, 'selected original or edited user wording survives GFM conversion'
            from supporting_gfm import load_gfm_record
            from render_profile import plain_text, walk
            from test_lantern_gfm import native_code, native_math, fixture_tables, parsed_tables
            ast, anchors, *_ = load_gfm_record(markdown)
            assert [plain_text(ast['blocks'][anchors[identifier]]['c'][2]) for identifier in ids] == ['User','Assistant']
            native_text={'passages':[{'text':part} for part in expected_text]}
            assert native_code(native_text) == [node['c'][1] for node in walk(ast['blocks']) if node['t']=='CodeBlock']
            assert native_math(native_text) == [(node['c'][0]['t'],node['c'][1].strip())
                                               for node in walk(ast['blocks']) if node['t']=='Math']
            assert fixture_tables(native_text) == parsed_tables(ast)
            assert 'SYNTHETIC HIDDEN CONTEXT' not in text
            assert 'SYNTHETIC TOOL OUTPUT' not in text
            for identifier, key in zip(ids, selected):
                # The importer now preserves the export's reviewed speaker on
                # its existing message anchor. Check the exact role rather
                # than accepting the old unannotated spelling or any role.
                role = fixture['mapping'][key]['message']['author']['role']
                assert role in {'user', 'assistant'}
                assert f'<a name="{identifier}" data-press-speaker="{role}"></a>' in text
            if live:
                from datetime import datetime, timezone
                for key in selected:
                    instant = datetime.fromtimestamp(fixture['mapping'][key]['message']['create_time'], timezone.utc).isoformat().replace('+00:00', 'Z')
                    assert '<time datetime="'+instant+'">' in text
                assert 'Model: gpt-6-thinking' in text
            expected_marker = ({'original-answer': 'K7M2Q9RX', 'regenerated-answer': 'K7M4Q2ZX',
                                'edited-answer': 'K7M2Q9TX'} if live else {
                                'a1': 'Original answer.', 'a2': 'Regenerated answer.', 'a3': 'Edited branch answer.'})[tip]
            assert expected_marker in text
            if live:
                assert 'Unsupported message content omitted' not in text
                assert 'Synthetic hidden system context.' not in text
                diagnostics = json.loads(conversion.stderr.split('GFM diagnostics: ', 1)[1])
                assert 'unsupported-message-content-omitted' not in diagnostics
    print(f'Independent ZIP/CRC/SHA checks and both import commands passed for {len(archives)} real downloads.', flush=True)


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='press-capture-import-') as scratch:
        result = subprocess.run(['node', '--test', 'tests/test_chatgpt_capture_browser.mjs'], cwd=ROOT,
                                env={**os.environ, 'PRESS_CAPTURE_OUTPUT_DIR': scratch})
        if result.returncode:
            raise SystemExit(result.returncode)
        verify(Path(scratch))
