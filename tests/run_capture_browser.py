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
FIXTURE = json.loads((ROOT / 'tests/fixtures/capture/conversation.json').read_text())
AUDIT_UNAVAILABLE = {'audit-unavailable', 'stalled', 'rate-limit'}
SUCCESS = {'happy', 'cookie-only', 'omission', 'hostile-page', 'multiple-tabs',
           'popup-close'} | AUDIT_UNAVAILABLE


def command(target, **variables):
    result = subprocess.run(['make', '--no-print-directory', target, f'PYTHON={sys.executable}'],
                            cwd=ROOT, env={**os.environ, **{key: str(value) for key, value in variables.items()}},
                            text=True, capture_output=True)
    if result.returncode:
        raise AssertionError(f'{target} failed: {result.stderr}')
    return result.stdout


def verify(directory):
    archives = list(directory.glob('*.zip'))
    assert {archive.stem for archive in archives} == SUCCESS
    for archive in archives:
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
            assert json.loads(native_bytes) == [FIXTURE], 'all forks and private context retained without synthesis'
            report = json.loads(contents['capture-manifest.json'])
            assert report['schema'] == 'press-chatgpt-capture-reference/v1'
            assert report['native_graph']['sha256_conversations_json'] == hashlib.sha256(native_bytes).hexdigest()
            assert report['native_graph']['mapping_nodes'] == 8
            assert report['native_graph']['branch_points'] == 2
            assert report['native_graph']['leaves'] == 3
            audit = report['selected_branch_audit']
            assert audit['status'] == ('unavailable' if archive.stem in AUDIT_UNAVAILABLE else 'audited')
            if audit['status'] == 'audited':
                pages = json.loads(contents['selected-branch-pages.json'])
                assert len(pages) == audit['page_count'] == 2
                assert pages[0]['messages'] == [FIXTURE['mapping']['a2']['message']]
                assert pages[1]['messages'][0] == FIXTURE['mapping']['u']['message']
                assert pages[-1]['page_info']['has_previous_page'] is False
                assert audit['selected_branch_messages_absent_from_native_mapping'] == (archive.stem == 'omission')
                assert audit['pagination_reached_oldest_page'] is True
            assert report['capture_limits']['response_bytes'] <= report['capture_limits']['max_response_bytes']

        listing = command('chat-list', FILE=archive)
        assert json.loads(listing) == [FIXTURE['conversation_id'], FIXTURE['title']]
        for tip, selected in (('a1', ['u', 'a1']), ('a2', ['u', 'a2']), ('a3', ['edited-u', 'a3'])):
            output = directory / f'{archive.stem}-{tip}.json'
            command('chat-extract', FILE=archive, CONVERSATION=FIXTURE['conversation_id'], TIP=tip, OUTPUT=output)
            passages = json.loads(output.read_text())['passages']
            expected_text = [FIXTURE['mapping'][key]['message']['content']['parts'][0] for key in selected]
            assert [passage['text'] for passage in passages] == expected_text
            assert [passage['label'] for passage in passages] == ['User', 'Assistant']
            assert all(passage['id'].startswith('m-') for passage in passages)
            markdown = directory / f'{archive.stem}-{tip}.md'
            command('chat-gfm', FILE=archive, CONVERSATION=FIXTURE['conversation_id'], TIP=tip, OUTPUT=markdown)
            text = markdown.read_text()
            assert 'SYNTHETIC HIDDEN CONTEXT' not in text
            assert 'SYNTHETIC TOOL OUTPUT' not in text
            for passage in passages:
                assert f'id="{passage["id"]}"' in text
            expected_marker = {'a1': 'Original answer.', 'a2': 'Regenerated answer.', 'a3': 'Edited branch answer.'}[tip]
            assert expected_marker in text
    print(f'Independent ZIP/CRC/SHA checks and all three import commands passed for {len(archives)} real downloads.', flush=True)


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='press-capture-import-') as scratch:
        result = subprocess.run(['node', '--test', 'tests/test_chatgpt_capture_browser.mjs'], cwd=ROOT,
                                env={**os.environ, 'PRESS_CAPTURE_OUTPUT_DIR': scratch})
        if result.returncode:
            raise SystemExit(result.returncode)
        verify(Path(scratch))
