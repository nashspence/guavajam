"""Independent native archive ZIP, CRC, digest and disclosure qualification."""
import hashlib
import json
from pathlib import Path
import zipfile
ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / 'tests/fixtures'
FIXTURE = json.loads((FIXTURES / 'capture/conversation.json').read_text())
LIVE_SHAPE = json.loads((FIXTURES / 'capture/live-shape.json').read_text())
AUDIT_UNAVAILABLE = {'audit-unavailable', 'stalled', 'rate-limit'}
SUCCESS = {'happy', 'cookie-only', 'omission', 'hostile-page', 'multiple-tabs',
           'popup-close', 'live-shape'} | AUDIT_UNAVAILABLE


def verify(directory):
    archives = list(directory.glob('*.zip'))
    assert {archive.stem for archive in archives} == SUCCESS
    for archive in archives:
        fixture = LIVE_SHAPE['native'] if archive.stem == 'live-shape' else FIXTURE
        live = archive.stem == 'live-shape'
        with zipfile.ZipFile(archive) as package:
            assert package.testzip() is None, 'independent CRC and ZIP directory validation'
            expected = {'conversations.json', 'archive-manifest.json'}
            if archive.stem not in AUDIT_UNAVAILABLE:
                expected.add('selected-branch-pages.json')
            assert len(package.infolist()) == len(expected)
            assert set(package.namelist()) == expected
            contents = {name: package.read(name) for name in expected}
            assert all(secret not in data for data in contents.values()
                       for secret in (b'synthetic-only-token', b'synthetic-cookie', b'fixture-account'))
            native_bytes = contents['conversations.json']
            assert json.loads(native_bytes) == [fixture], 'all forks and private context retained without synthesis'
            report = json.loads(contents['archive-manifest.json'])
            assert report['schema'] == 'chatgpt.conversation-archive.v1'
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

    print('Native archive ZIP, digest and disclosure checks passed.', flush=True)
