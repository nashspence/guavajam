"""Captured reference evidence must resolve links without inventing destinations."""
from pathlib import Path
import json
import sys
import tempfile
import unittest

from guavajam_core.chat_gfm import review_gfm
from guavajam_core.chat_references import component_inlines, reference_index
from guavajam_core.extract_chatgpt import branch_messages, transcript
from guavajam_core.source_ast import plain_text, walk
from guavajam_core.supporting_gfm import load_gfm_record

URL = 'https://docs.python.org/3/library/csv.html?utm_source=chatgpt.com'
REF = 'turn655023search0'


def message_reference(url=URL):
    return {'author': {'role': 'tool'}, 'content': {'content_type': 'text', 'parts': ['Private tool output']},
            'metadata': {'search_result_groups': [{'entries': [{
                'ref_id': {'turn_index': 655023, 'ref_type': 'search', 'ref_index': 0},
                'url': url, 'title': 'Python csv module', 'snippet': 'Private snippet'}]}]}}


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.references = reference_index([message_reference()])

    def parse(self, source, references=None):
        record = {'title': 'Reference review', 'scope': 'Synthetic qualification.',
                  'passages': [{'id': 'reply', 'label': 'Assistant', 'text': source}]}
        data, diagnostics = review_gfm(record, references=references if references is not None else self.references)
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / 'review.md'; path.write_bytes(data)
            ast, *_ = load_gfm_record(path)
        return data, diagnostics, ast

    def test_direct_dynamic_single_multiple_and_native_citations_resolve(self):
        source = ('<Link title="CSV &amp; quoting" url="https://docs.python.org/3/library/csv.html"/>\n\n'
                  'Read <Link url={turn655023search0.url.uri} title="Python csv module"/>.\n\n'
                  '<Cite ref="turn655023search0"/>\n\n'
                  '<Cite refs={["turn655023search0","turn655023search0"]}/>\n\n'
                  'Before \ue200cite\ue202turn655023search0\ue201 after.\n')
        data, diagnostics, ast = self.parse(source)
        links = [node for node in walk(ast['blocks']) if node['t'] == 'Link']
        self.assertEqual(5, len(links))
        self.assertEqual(URL, links[-1]['c'][2][0])
        self.assertEqual('CSV & quoting', plain_text(links[0]['c'][1]))
        self.assertEqual(5, diagnostics['chatgpt-reference-resolved'])
        self.assertNotIn(b'<Cite', data); self.assertNotIn(b'<Link', data)
        self.assertNotIn(b'turn655023', data)
        self.assertIn(b'Before ', data); self.assertIn(b' after.', data)

    def test_citation_aliases_collapse_per_group_in_source_order_for_both_native_forms(self):
        other = 'https://docs.python.org/3/library/io.html'
        references = {**self.references,
            'turn1view0': {'url': URL, 'title': 'Another title for the same page'},
            'turn2search0': {'url': URL, 'title': 'CSV once more'},
            'turn3view0': {'url': other, 'title': 'Streams'}}
        ids = [REF, 'turn3view0', 'turn1view0', 'turn2search0', 'turn3view0']
        source = ('<Cite refs={' + json.dumps(ids, separators=(',', ':')) + '}/>\n\n'
                  'Another claim \ue200cite' + ''.join('\ue202' + identifier for identifier in ids) + '\ue201.\n\n'
                  '<Link url={turn655023search0.url.uri} title="Independent CSV link"/>')
        data, diagnostics, ast = self.parse(source, references)
        links = [node for node in walk(ast['blocks']) if node['t'] == 'Link']
        self.assertEqual([URL, other, URL, other, URL], [node['c'][2][0] for node in links])
        self.assertEqual(['Python csv module', 'Streams'] * 2 + ['Independent CSV link'],
                         [plain_text(node['c'][1]) for node in links])
        self.assertEqual(3, diagnostics['chatgpt-reference-resolved'])
        self.assertNotIn(b'Another title', data)
        self.assertNotIn(b'CSV once more', data)

    def test_same_title_different_page_fragment_or_query_stays_distinct(self):
        urls = [URL, URL + '#reader-objects', URL + '#writer-objects',
                'https://docs.python.org/3/library/csv.html?dialect=excel',
                'https://docs.python.org/3/library/io.html']
        ids = [f'turn7view{i}' for i in range(len(urls))]
        references = {key: {'url': url, 'title': 'Same title'} for key, url in zip(ids, urls)}
        _, _, ast = self.parse('<Cite refs={' + json.dumps(ids, separators=(',', ':')) + '}/>', references)
        self.assertEqual(urls, [node['c'][2][0] for node in walk(ast['blocks']) if node['t'] == 'Link'])

    def test_alias_deduplication_does_not_hide_unresolved_or_invalid_evidence(self):
        for target in (None, {'url': URL, 'title': 'Bad\ntitle'},
                       {'url': 'javascript:alert(1)', 'title': 'Unsafe'}):
            references = {**self.references, 'turn1view0': target}
            source = '<Cite refs={["turn655023search0","turn655023search0","turn1view0"]}/>'
            with self.subTest(target=target):
                self.assertIsNone(component_inlines(source, references))
                _, diagnostics, ast = self.parse(source, references)
                self.assertEqual(1, diagnostics['unresolved-chatgpt-citation'])
                self.assertFalse(any(node['t'] == 'Link' for node in walk(ast['blocks'])))

    def test_unsafe_missing_conflicting_or_executable_references_stay_unresolved(self):
        conflicting = reference_index([message_reference(), message_reference('https://example.org/different')])
        self.assertIsNone(conflicting[REF])
        for reference in ({}, conflicting, reference_index([message_reference('javascript:alert(1)')])):
            self.assertIsNone(component_inlines('<Cite ref="turn655023search0"/>', reference))
        for source in ('<Link url={dangerous()} title="Do not evaluate"/>',
                       '<Link url="javascript:alert(1)" title="Unsafe"/>',
                       '<Link url="sandbox:/mnt/data/private.csv" title="Attachment"/>',
                       '<Link url="{turn655023search0.url.uri}" title="Literal braces"/>',
                       '<Link url={turn655023search0.url.uri} title={dangerous()}/>',
                       '<Link url="https://example.org" title="One" title="Two"/>',
                       '<Cite refs={["turn655023search0","turn999view0"]}/>'):
            with self.subTest(source=source):
                self.assertIsNone(component_inlines(source, self.references))
                data, diagnostics, ast = self.parse(source)
                self.assertFalse(any(n['t'] == 'Link' for n in walk(ast['blocks'])))
                self.assertTrue(any(key.startswith('unresolved-chatgpt-') for key in diagnostics))
        self.assertNotIn('Private', str(self.references))

    def test_code_is_literal_and_missing_native_citations_are_disclosed(self):
        component = '<Link url={turn655023search0.url.uri} title="Literal code"/>'
        fence = chr(96) * 3
        source = fence + 'jsx\n' + component + '\n' + fence + '\n\nMissing \ue200cite\ue202turn999view0\ue201.\n'
        data, diagnostics, ast = self.parse(source)
        self.assertIn(component.encode(), data)
        self.assertEqual([component], [n['c'][1] for n in walk(ast['blocks']) if n['t'] == 'CodeBlock'])
        self.assertEqual(1, diagnostics['unresolved-chatgpt-citation'])
        self.assertNotIn('chatgpt-reference-resolved', diagnostics)

    def test_only_selected_ancestry_can_supply_reference_evidence(self):
        visible = {'author': {'role': 'assistant'},
                   'content': {'content_type': 'text', 'parts': [
                       '<Link url={turn655023search0.url.uri} title="CSV"/>']}}
        conversation = {'title': 'Selected ancestry', 'mapping': {
            'root': {'parent': None, 'message': None},
            'tool': {'parent': 'root', 'message': message_reference()},
            'tip': {'parent': 'tool', 'message': visible},
            'sibling': {'parent': 'root', 'message': message_reference('https://example.org/sibling')},
        }}
        refs = reference_index(m for _, m in branch_messages(conversation, 'tip'))
        self.assertEqual(URL, refs[REF]['url'])
        record = transcript(conversation, 'tip')
        self.assertNotIn('Private tool output', str(record))
        self.assertNotIn('Private snippet', str(record))
        self.assertIn(URL.encode(), review_gfm(record, references=refs)[0])
        conversation['mapping']['tip']['parent'] = 'root'
        self.assertEqual({}, reference_index(m for _, m in branch_messages(conversation, 'tip')))


if __name__ == '__main__':
    unittest.main()


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(loader.loadTestsFromName(name,cls) for cls in (ReferenceTests,) for name in cls.__dict__ if name.startswith("test_"))
