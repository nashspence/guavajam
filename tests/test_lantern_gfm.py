"""Qualify native Lantern text through review, publication and independent parsing."""
import ast as python_ast
import copy
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_policy import AssetError
from chat_gfm import review_gfm
from extract_chatgpt import branch_messages, transcript
from chat_references import reference_index
from render_profile import plain_text, walk
from supporting_gfm import load_gfm_record
from validate_sources import git_sources, validate_repository
from verify_edition import verify_edition
from test_assets import EditionTests

FIXTURE = ROOT / 'tests/fixtures/chatgpt/lantern.json'
TIPS = ('node-126', 'node-137')
GFM_FIXTURES = dict(zip(TIPS, ('lantern-current.gfm.md', 'lantern-alternate.gfm.md')))


@lru_cache(maxsize=2)
def converted(tip):
    conversation, = json.loads(FIXTURE.read_text())
    record = transcript(conversation, tip)
    data, diagnostics = review_gfm(record, identity=conversation['conversation_id'], references=references(tip))
    return record, data, diagnostics


def references(tip):
    conversation, = json.loads(FIXTURE.read_text())
    return reference_index(message for _, message in branch_messages(conversation, tip))


def native_math(record):
    pattern = r'\\\((?P<inline>.*?)\\\)|\\\[(?P<display>.*?)\\\]'
    return [('InlineMath' if match['inline'] is not None else 'DisplayMath',
             (match['inline'] if match['inline'] is not None else match['display']).strip())
            for passage in record['passages']
            for match in re.finditer(pattern, passage['text'], re.S)]


def native_code(record):
    result = []
    patterns = (
        r'^<CodeBlock language="[^"]+"(?: editable)?>\n(.*?)^</CodeBlock>[ \t]*$',
        r'^```[^\n]*\n(.*?)^```[ \t]*$',
    )
    for passage in record['passages']:
        matches = [match for pattern in patterns for match in re.finditer(pattern, passage['text'], re.M | re.S)]
        result.extend(match[1].removesuffix('\n') for match in sorted(matches, key=lambda m: m.start()))
    return result


def fixture_tables(record):
    result = []
    for passage in record['passages']:
        for match in re.finditer(r'(?m)^\|[^\n]+\n\|[ :|\-]+\|\n(?:\|[^\n]+\n?)+', passage['text']):
            rows = match[0].splitlines()
            cells = [[cell.strip().replace('`', '').replace('\\(', '').replace('\\)', '')
                      for cell in row.strip('|').split('|')] for row in [rows[0], *rows[2:]]]
            cells = [[re.sub(r'<icon\b[^<>\r\n]*/>', '□', cell) for cell in row] for row in cells]
            result.append(cells)
    return result


def parsed_tables(ast):
    result = []
    for table in (node for node in walk(ast['blocks']) if node['t'] == 'Table'):
        c = table['c']
        rows = [*c[3][1], *(row for body in c[4] for row in [*body[2], *body[3]]), *c[5][1]]
        result.append([[plain_text(cell[4]) for cell in row[1]] for row in rows])
    return result


class LanternConversionTests(unittest.TestCase):
    def test_native_visible_text_and_privacy_projection(self):
        conversation, = json.loads(FIXTURE.read_text())
        self.assertEqual({'conversation_id', 'title', 'current_node', 'mapping'}, set(conversation))
        self.assertEqual(138, len(conversation['mapping']))
        self.assertEqual(set(TIPS), {key for key, node in conversation['mapping'].items() if not node['children']})
        parts = []
        for node in conversation['mapping'].values():
            self.assertEqual({'id', 'parent', 'children', 'message'}, set(node))
            message = node['message']
            if message is None:
                continue
            self.assertLessEqual(set(message), {'id', 'author', 'content', 'channel', 'recipient', 'metadata', 'create_time'})
            self.assertEqual(1577836800 + 60 * int(node['id'].split('-')[1]), message['create_time'])
            self.assertEqual({'role'}, set(message['author']))
            self.assertLessEqual(set(message.get('metadata', {})),
                                 {'is_visually_hidden_from_conversation', 'is_user_system_message',
                                  'model_slug', 'resolved_model_slug', 'thinking_effort', 'search_result_groups'})
            if 'thinking_effort' in message.get('metadata', {}):
                self.assertEqual('max', message['metadata']['thinking_effort'])
            for group in message.get('metadata', {}).get('search_result_groups', []):
                self.assertEqual({'entries'}, set(group))
                for entry in group['entries']:
                    self.assertEqual({'ref_id', 'url', 'title'}, set(entry))
                    self.assertEqual({'turn_index', 'ref_type', 'ref_index'}, set(entry['ref_id']))
                    self.assertTrue(entry['url'].startswith(('https://docs.python.org/', 'https://www.itl.nist.gov/', 'https://itl.nist.gov/')))
            if 'parts' in message['content'] and message['content']['parts'] != ['Synthetic excluded message.']:
                parts.append(message['content']['parts'])
        self.assertEqual(28, len(parts))
        self.assertEqual('ca7789692b463c68ce45b5a6a2ad41f2e586268438fe042fd91c2e2cd188d087',
                         hashlib.sha256(json.dumps(parts, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest())

    def test_both_branches_preserve_all_equations_tables_and_code(self):
        with tempfile.TemporaryDirectory() as scratch:
            for tip, count in zip(TIPS, (45, 61)):
                with self.subTest(tip=tip):
                    record, data, diagnostics = converted(tip)
                    self.assertEqual(FIXTURE.with_name(GFM_FIXTURES[tip]).read_bytes(), data,
                                     'committed GitHub review fixtures must match the converter output')
                    # GitHub's native GFM reading surface now distinguishes
                    # responder turns without any publisher-provided CSS.
                    self.assertEqual(sum(p.get('speaker') == 'assistant' for p in record['passages']),
                                     data.count(b'data-press-speaker="assistant"'))
                    self.assertNotIn(b'\n## Assistant\n', data)
                    self.assertNotIn(b'\n## User\n', data)
                    self.assertNotIn(b'data-press-speaker="user"', data)
                    credits = sum(p.get('speaker') == 'assistant' for p in record['passages'])
                    self.assertEqual(credits, data.count(
                        '— gpt-6-thinking (max effort)\n'.encode()))
                    self.assertNotIn('— Assistant'.encode(), data)
                    self.assertNotIn(b'Model:', data)
                    self.assertNotIn(b'Thinking effort:', data)
                    self.assertNotIn('— User'.encode(), data)
                    # Setext-looking TeX within quoted $$ stays mathematical,
                    # not a spurious Markdown heading. Its AST is compared below.
                    if tip == TIPS[1]:
                        self.assertIn(b'>     =\n', data)
                    self.assertEqual((data, diagnostics), review_gfm(record, identity='lantern-qualification', references=references(tip)))
                    self.assertNotIn(b'[Unsupported message content omitted]', data)
                    self.assertTrue(all('timestamp' in passage for passage in record['passages']))
                    self.assertTrue(all(passage['label'] in {'User', 'Assistant'} for passage in record['passages']))
                    for passage in record['passages']:
                        if passage['label'] == 'Assistant':
                            self.assertEqual('gpt-6-thinking', passage['model'])
                            self.assertEqual('max', passage['thinking_effort'])
                    self.assertEqual(2, diagnostics['chatgpt-divider-normalized'])
                    path = Path(scratch) / (tip + '.md'); path.write_bytes(data)
                    parsed, anchors, _, _ = load_gfm_record(path)
                    math = [(node['c'][0]['t'], node['c'][1].strip())
                            for node in walk(parsed['blocks']) if node['t'] == 'Math']
                    self.assertEqual(count, len(math))
                    self.assertEqual(native_math(record), math)
                    actual_code = [node['c'][1] for node in walk(parsed['blocks']) if node['t'] == 'CodeBlock']
                    expected_code = native_code(record)
                    self.assertGreaterEqual(len(expected_code), 4)
                    for body in expected_code:
                        self.assertIn(body, actual_code)
                    self.assertEqual(6 if tip == TIPS[0] else 11, len(actual_code))
                    original = '\n'.join(passage['text'] for passage in record['passages'])
                    tooltip_icons = [dict(node['c'][0][2]).get('title', '') for node in walk(parsed['blocks'])
                                     if node['t'] == 'Span' and dict(node['c'][0][2]).get('title', '').startswith('<icon ')]
                    literal_icons = [icon for body in actual_code for icon in re.findall(r'<icon\b[^<>\r\n]*/>', body)]
                    self.assertCountEqual(re.findall(r'<icon\b[^<>\r\n]*/>', original), tooltip_icons + literal_icons,
                                          'each icon remains exact in hover metadata or literal code')
                    for body in actual_code:
                        self.assertTrue(body in original or textwrap.indent(body, '    ') in original,
                                        'every code block retains exact source text, including literal layout components')
                    self.assertEqual(fixture_tables(record), parsed_tables(parsed))
                    self.assertEqual(11, len(parsed_tables(parsed)))
                    python = next(body for body in expected_code if 'import csv' in body)
                    python_ast.parse(python)
                    self.assertIn('Café', python); self.assertIn('Δ', python)
                    self.assertGreater(len(anchors), 250)
                    self.assertEqual(2, diagnostics['chatgpt-codeblock-normalized'])
                    self.assertEqual(2, diagnostics['unsupported-rich-component-as-literal-code'])
                    self.assertEqual(1, diagnostics['image-or-attachment-omitted'])
                    self.assertEqual(9 if tip == TIPS[0] else 13, diagnostics['chatgpt-reference-resolved'])
                    self.assertGreater(diagnostics['presentation-wrapper-retained-in-tooltip'], 5)
                    self.assertGreater(diagnostics['unsupported-icon-retained-in-tooltip'], 1)
                    links = [node['c'][2][0] for node in walk(parsed['blocks']) if node['t'] == 'Link']
                    encoding_claims = [node for node in walk(parsed['blocks']) if node['t'] in {'Para', 'Plain'}
                                       and plain_text(node).startswith('Text encoding should be handled explicitly')]
                    self.assertEqual(1 if tip == TIPS[0] else 0, len(encoding_claims))
                    for claim in encoding_claims:
                        self.assertEqual(['https://docs.python.org/3/library/csv.html'],
                                         [node['c'][2][0] for node in walk(claim) if node['t'] == 'Link'])
                    self.assertIn('https://docs.python.org/3/library/csv.html', links)
                    self.assertTrue(any('nist.gov/' in link for link in links))
                    self.assertNotIn(b'Synthetic excluded message.', data)
                    self.assertFalse(any(node['t'] in {'RawBlock', 'RawInline', 'Image', 'Note'}
                                         for node in walk(parsed['blocks'])))

    def test_real_make_zip_import_is_private_new_deterministic_and_branch_specific(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch); package = root / 'export.zip'
            with zipfile.ZipFile(package, 'w') as archive:
                archive.writestr('conversations.json', FIXTURE.read_bytes())
            for tip in TIPS:
                output = root / (tip + '.md')
                command = ['make', '--no-print-directory', 'chat-gfm', f'PYTHON={sys.executable}']
                env = {**os.environ, 'FILE': str(package), 'CONVERSATION': 'lantern-qualification',
                       'TIP': tip, 'OUTPUT': str(output)}
                run = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True)
                self.assertEqual(0, run.returncode, run.stderr)
                self.assertEqual(converted(tip)[1], output.read_bytes())
                self.assertEqual(0o600, output.stat().st_mode & 0o777)
                self.assertIn('chatgpt-reference-resolved', run.stderr)
                self.assertNotEqual(0, subprocess.run(command, cwd=ROOT, env=env, capture_output=True).returncode)
                self.assertEqual(converted(tip)[1], output.read_bytes())

    def test_component_normalization_preserves_literal_fences_and_rejects_ambiguity(self):
        code = '# Literal heading\nprint(r"\\(literal\\)")\n~~~\n</CodeBlock> is text\n'
        text = ('<CodeBlock language="python" editable>\n' + code + '</CodeBlock>\n\n'
                '````markdown\n<CodeBlock language="python">\nnot a component\n````\n\n'
                '`<CodeBlock language="python">`\n\n'
                '    <CodeBlock language="python">\n    indented literal\n\n'
                '<widget><Link url={turn0.url.uri} title="Unresolved"/></widget>\n')
        record = {'title': 'Literal qualification', 'scope': 'Synthetic',
                  'passages': [{'id': 'literal', 'label': 'Assistant', 'text': text}]}
        data, diagnostics = review_gfm(record)
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / 'review.md'; path.write_bytes(data)
            parsed, _, _, _ = load_gfm_record(path)
        blocks = [node['c'][1] for node in walk(parsed['blocks']) if node['t'] == 'CodeBlock']
        self.assertIn(code.removesuffix('\n'), blocks)
        self.assertEqual(1, diagnostics['chatgpt-codeblock-normalized'])
        self.assertEqual(2, diagnostics['adjacent-inline-code-merged'])
        for text in ('<CodeBlock language="python">\nunclosed\n',
                     'Prose\n<CodeBlock language="python">\nnot standalone\n</CodeBlock>\n'):
            record['passages'][0]['text'] = text
            with self.assertRaises(AssetError): review_gfm(record)


class LanternPublicationTests(EditionTests):
    def source(self):
        directory = self.repo / 'works/sample/supporting'; directory.mkdir()
        for name, tip in zip(('current', 'alternate'), TIPS):
            (directory / (name + '.md')).write_bytes(converted(tip)[1])
        with (self.repo / 'works/sample/manuscript.md').open('a') as stream:
            stream.write('\nRead the [current branch](supporting/current.md) and '
                         '[alternate branch](supporting/alternate.md).\n')
        self.git('add', 'works'); self.git('commit', '-qm', 'Review both Lantern branches')
        self.commit = self.git('rev-parse', 'HEAD')

    def test_both_branches_publish_and_verify_with_exact_reviewed_sources(self):
        self.source()
        self.assertEqual([], validate_repository(self.repo, git_sources(self.repo)))
        manifest = self.build()
        self.assertEqual(manifest, verify_edition(self.output))
        self.assertEqual(2, len(manifest['supporting_documents']))
        page = (self.output / manifest['canonical_artifact']).read_text()
        for document in manifest['supporting_documents']:
            expected = (self.repo / document['source_path']).read_bytes()
            self.assertEqual(hashlib.sha256(expected).hexdigest(), document['sha256'])
            self.assertGreater(len(document['anchors']), 250)
        self.assertGreater(page.count('<math'), 106)
        self.assertNotIn('<CodeBlock', page); self.assertNotIn('<Chart', page)
        self.assertIn('Café', page); self.assertIn('Δ', page)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(loader.loadTestsFromName(name, cls) for cls in (
        LanternConversionTests, LanternPublicationTests) for name in cls.__dict__ if name.startswith('test_'))
