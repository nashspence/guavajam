"""Qualify built archive downloads through an isolated installed extractor."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT / 'tests/support'))
sys.path.insert(0,str(ROOT / 'some-implementations/a-chatgpt-conversation-archive-gfm-extractor/tests'))
from guavajam_test_support import FIXTURES
FIXTURE = json.loads((FIXTURES / 'capture/conversation.json').read_text())
LIVE_SHAPE = json.loads((FIXTURES / 'capture/live-shape.json').read_text())

def command(target, **variables):
    args=[sys.executable,'-m','a_chatgpt_conversation_archive_gfm_extractor']
    if target == 'list': args += ['--list']
    else: args += ['--conversation',str(variables['CONVERSATION']),'--tip',str(variables['TIP']),'--output',str(variables['OUTPUT'])]
    result=subprocess.run([*args,str(variables['FILE'])],capture_output=True,text=True)
    if result.returncode: raise AssertionError('Archive extractor command failed: '+result.stderr)
    return result

def verify(directory):
    archives=list(directory.glob('*.zip'))
    assert archives
    for archive in archives:
        fixture = LIVE_SHAPE['native'] if archive.stem == 'live-shape' else FIXTURE
        live = archive.stem == 'live-shape'
        listing = command('list', FILE=archive)
        assert json.loads(listing.stdout) == [fixture['conversation_id'], fixture['title']]
        tips = (('original-answer', ['original-prompt', 'original-answer']),
                ('regenerated-answer', ['original-prompt', 'regenerated-answer']),
                ('edited-answer', ['edited-prompt', 'edited-answer'])) if live else (
                ('a1', ['u', 'a1']), ('a2', ['u', 'a2']), ('a3', ['edited-u', 'a3']))
        for tip, selected in tips:
            expected_text = [fixture['mapping'][key]['message']['content']['parts'][0] for key in selected]
            ids = ['m-' + hashlib.sha256(key.encode()).hexdigest() for key in selected]
            markdown = directory / f'{archive.stem}-{tip}.md'
            conversion = command('extract', FILE=archive, CONVERSATION=fixture['conversation_id'], TIP=tip, OUTPUT=markdown)
            text = markdown.read_text()
            assert expected_text[0] in text, 'selected original or edited user wording survives GFM conversion'
            from guavajam.supporting_gfm import load_gfm_record
            from guavajam.source_ast import plain_text, walk
            from test_lantern_gfm import native_code, native_math, fixture_tables, parsed_tables
            ast, anchors, *_ = load_gfm_record(markdown)
            assert ast['blocks'][anchors[ids[0]]]['t'] in {'Para','Header','Div'}
            assert ast['blocks'][anchors[ids[1]]]['t'] == 'Div'
            assert 'guavajam-reviewed-turn-start' in ast['blocks'][anchors[ids[1]]]['c'][0][1]
            native_text={'passages':[{'text':part} for part in expected_text]}
            assert native_code(native_text) == [node['c'][1] for node in walk(ast['blocks']) if node['t']=='CodeBlock']
            assert native_math(native_text) == [(node['c'][0]['t'],node['c'][1].strip())
                                               for node in walk(ast['blocks']) if node['t']=='Math']
            assert fixture_tables(native_text) == parsed_tables(ast)
            assert 'SYNTHETIC HIDDEN CONTEXT' not in text
            assert 'SYNTHETIC TOOL OUTPUT' not in text
            for identifier, key in zip(ids, selected):
                # Authorial text is the unquoted default. Only reviewed external
                # responses receive an explicit role and trailing attribution.
                role = fixture['mapping'][key]['message']['author']['role']
                assert role in {'user', 'assistant'}
                if role == 'assistant':
                    assert f'<a name="{identifier}" data-guavajam-speaker="assistant"></a>' in text
                else:
                    assert f'<a name="{identifier}"></a>' in text
            assert '— ' in text and '> ' in text and '— Assistant' not in text
            assert 'data-guavajam-speaker="user"' not in text
            assert '## User' not in text and '## Assistant' not in text
            assert '<time datetime=' not in text
            assert 'Model: gpt-6-thinking' not in text
            expected_marker = ({'original-answer': 'K7M2Q9RX', 'regenerated-answer': 'K7M4Q2ZX',
                                'edited-answer': 'K7M2Q9TX'} if live else {
                                'a1': 'Original answer.', 'a2': 'Regenerated answer.', 'a3': 'Edited branch answer.'})[tip]
            assert expected_marker in text
            if live:
                assert 'Unsupported message content omitted' not in text
                assert 'Synthetic hidden system context.' not in text
                diagnostics = json.loads(conversion.stderr.split('GFM diagnostics: ', 1)[1])
                assert 'unsupported-message-content-omitted' not in diagnostics
    print('Archive producer and GFM consumer handoff passed.',flush=True)

if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='chatgpt-archive-handoff-') as scratch:
        subprocess.run([sys.executable,str(ROOT / 'some-implementations/a-chatgpt-conversation-archiver-browser-extension/tools/run_browser.py'),
            '--output',scratch],check=True)
        verify(Path(scratch))
