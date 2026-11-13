"""Reviewed GFM owns publication text, citation coverage and source custody."""
import copy
import hashlib
import io
import json
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import Mock

from guavajam_test_support import FIXTURES, RESOURCES
ROOT = Path(__file__).resolve().parents[1]
from guavajam_core.asset_policy import AssetError, verify_bytes
from guavajam_core.supporting_gfm import load_gfm_record, timestamp_label, valid_timestamp
from guavajam_core.source_ast import plain_text, walk
from guavajam_core.validate_sources import git_sources, validate_repository
from guavajam_core.manage_assets import upload


def document(body):
    return '<a name="document"></a>\n\n# Evidence\n\n' + body.rstrip() + '\n'


class SourceContractTests(unittest.TestCase):
    def parse(self, source):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / 'note.md'; path.write_text(source)
            return load_gfm_record(path)

    def test_every_top_level_content_block_requires_its_own_explicit_anchor(self):
        for block in ('Unanchored prose.', '$$\nx=x\n$$', '```python\nprint(1)\n```',
                      '| A |\n|---|\n| 1 |', '- First\n- Second', '> A quotation.', '---'):
            with self.subTest(block=block), self.assertRaisesRegex(AssetError, 'anchor'):
                self.parse(document(block))
            self.parse(document('<a name="element"></a>\n\n' + block))

    def test_lists_and_quotes_are_cited_as_whole_composites(self):
        source = document('<a name="sequence"></a>\n\n- First\n  - Nested\n\n'
                          '<a name="quotation"></a>\n\n> A quotation.\n>\n>     literal code\n')
        ast, anchors, *_ = self.parse(source)
        self.assertEqual({'document', 'sequence', 'quotation'}, set(anchors))
        self.assertEqual('BulletList', ast['blocks'][anchors['sequence']]['t'])
        self.assertEqual('BlockQuote', ast['blocks'][anchors['quotation']]['t'])
        with self.assertRaises(AssetError):
            self.parse(document('<a name="list"></a>\n\n- <a name="nested"></a>\n\n  Nested anchor\n'))

    def test_supporting_heading_skips_name_the_anchor_and_expected_level(self):
        for level in (3, 4, 5):
            with self.subTest(level=level), self.assertRaisesRegex(
                    AssetError, f'heading #deep skips H1 → H{level} in document outline; use H2'):
                self.parse(document('<a name="deep"></a>\n\n' + '#' * level + ' Deep heading\n'))
        source = document('<a name="section-anchor"></a>\n\n## Section\n\n'
                          '<a name="detail-anchor"></a>\n\n### Detail\n\n'
                          '<a name="next"></a>\n\n## Next section\n')
        ast, anchors, *_ = self.parse(source)
        self.assertEqual([1, 2, 3, 2], [b['c'][0] for b in ast['blocks'] if b['t'] == 'Header'])
        self.assertEqual({'document', 'section-anchor', 'detail-anchor', 'next'}, set(anchors))

    def test_quotation_heading_scopes_reset_without_changing_the_author_outline(self):
        def quote(headings):
            return ('<a name="reply" data-press-speaker="responder"></a>\n\n' +
                    '\n>\n'.join('> <a name="quoted-' + str(i) + '"></a>\n>\n> ' + '#' * level + ' Quoted heading'
                                 for i, level in enumerate(headings)) + '\n\n— Correspondent\n')
        author = ('<a name="section-anchor"></a>\n\n## Section\n\n'
                  '<a name="detail-anchor"></a>\n\n### Detail\n\n'
                  '<a name="subdetail-anchor"></a>\n\n#### Subdetail\n\n')
        source = document(author + quote([2, 3]) + '\n<a name="continue-anchor"></a>\n\n#### Continue\n')
        ast, anchors, *_ = self.parse(source)
        self.assertEqual([1, 2, 3, 4, 2, 3, 4], [b['c'][0] for b in ast['blocks'] if b['t'] == 'Header'])
        self.assertTrue({'reply', 'quoted-0', 'quoted-1', 'continue-anchor'} <= set(anchors))
        for levels, previous, target in (([4], 1, 4), ([2, 4], 2, 4)):
            with self.subTest(levels=levels), self.assertRaisesRegex(
                    AssetError, f'skips H{previous} → H{target} in quotation #reply'):
                self.parse(document(author + quote(levels)))
        # A quotation cannot supply a missing parent in the author's own outline.
        with self.assertRaisesRegex(AssetError, 'heading #continue-anchor skips H1 → H3 in document outline'):
            self.parse(document(quote([2]) + '\n<a name="continue-anchor"></a>\n\n### Continue\n'))

    def test_both_reviewed_lantern_branches_keep_their_heading_levels_and_anchors(self):
        for path in sorted((FIXTURES / 'chatgpt').glob('*.gfm.md')):
            with self.subTest(branch=path.name):
                ast, anchors, *_ = load_gfm_record(path)
                self.assertGreater(len(anchors), 100)
                self.assertEqual(1, sum(b['t'] == 'Header' and b['c'][0] == 1 for b in ast['blocks']))

    def test_author_is_plain_and_only_external_quotations_are_credited(self):
        source = document('<a name="author-entry"></a>\n\nThe author writes ordinarily.\n\n'
            '<a name="author-section"></a>\n\n## Actual section\n\n'
            '<a name="response" data-press-speaker="responder"></a>\n\n'
            '> <a name="response-body"></a>\n>\n> A quoted response.\n\n'
            '— Correspondent\n')
        ast, anchors, *_ = self.parse(source)
        self.assertEqual({'document', 'author-entry', 'author-section', 'response', 'response-body'},set(anchors))
        self.assertEqual('Para', ast['blocks'][anchors['author-entry']]['t'])
        self.assertEqual('Header', ast['blocks'][anchors['author-section']]['t'])
        self.assertFalse(any('data-press-speaker' in dict(node['c'][1][2]) for node in walk(ast['blocks'])
                             if node['t']=='Header'))
        self.assertEqual('Div', ast['blocks'][anchors['response']]['t'])
        attrs = dict(ast['blocks'][anchors['response']]['c'][0][2])
        self.assertEqual({'data-press-speaker':'responder', 'data-press-attribution':'Correspondent'},attrs)

        for bad in (
            '<a name="voice" data-press-speaker="user"></a>\n\nAuthorial text.\n',
            '<a name="voice" data-press-speaker="author"></a>\n\nAuthorial text.\n',
            '<a name="voice" data-press-speaker="assistant"></a>\n\n## Heading\n',
            '<a name="voice" data-press-speaker="responder"></a>\n\n'
                '> <a name="part"></a>\n>\n> Quoted text.\n',
            '<a name="voice" data-press-speaker="responder"></a>\n\n'
                '> <a name="part"></a>\n>\n> Quoted text.\n\n— Author\n',
            '<a name="voice" data-press-speaker="responder" style="color:red"></a>\n\n'
                '> <a name="part"></a>\n>\n> Quoted text.\n\n— Correspondent\n',
            '> <a name="voice" data-press-speaker="responder"></a>\n>\n> Text.\n',
        ):
            with self.subTest(source=bad[:80]), self.assertRaises(AssetError):
                self.parse(document(bad))

    def test_responder_quote_preserves_nested_quotation_and_source_ids(self):
        source=document('<a name="response" data-press-speaker="responder"></a>\n\n'
            '> <a name="paragraph"></a>\n>\n> Answer.\n>\n'
            '> <a name="inner-quote"></a>\n>\n> > Another quoted passage.\n>\n'
            '> <a name="code"></a>\n>\n> ```python\n> print(1)\n> ```\n\n'
            '— Correspondent\n')
        ast, anchors, *_ = self.parse(source)
        self.assertEqual({'document','response','paragraph','inner-quote','code'},set(anchors))
        self.assertEqual('Div',ast['blocks'][anchors['response']]['t'])
        self.assertEqual('BlockQuote',ast['blocks'][anchors['inner-quote']]['t'])
        self.assertEqual('CodeBlock',ast['blocks'][anchors['code']]['t'])
        for bad in (
            '<a name="response" data-press-speaker="responder"></a>\n\n'
            '> <a name="part"></a>\n>\n> A response.\n\n— Author\n',
            '<a name="response" data-press-speaker="responder"></a>\n\n'
            '> <a name="part"></a>\n>\n> A response.\n\n**— Correspondent**\n',
            '<a name="response" data-press-speaker="responder"></a>\n\n'
            '> A response.\n\n— Correspondent\n',
            '<a name="response" data-press-speaker="responder"></a>\n\n'
            '> <a name="part" data-press-speaker="responder"></a>\n>\n> Text.\n\n— Correspondent\n',
        ):
            with self.subTest(source=bad),self.assertRaises(AssetError):self.parse(document(bad))

    def test_external_credit_is_one_reviewed_entity_not_a_field_list(self):
        def reviewed(credit, speaker='responder'):
            return document('<a name="response" data-press-speaker="' + speaker + '"></a>\n\n'
                            '> <a name="part"></a>\n>\n> Quoted response.\n\n' + credit + '\n')
        valid = (
            '— Correspondent',
            '— gpt-6-thinking',
            '— Unidentified AI model',
            '— Unidentified AI model (high effort)',
            '— gpt-6-thinking (max effort)',
            '— model/path.v2+experimental (extra_high effort)',
        )
        for source_credit in valid:
            with self.subTest(credit=source_credit):
                ast, anchors, *_ = self.parse(reviewed(source_credit))
                attrs = dict(ast['blocks'][anchors['response']]['c'][0][2])
                self.assertEqual({'data-press-speaker': 'responder',
                                  'data-press-attribution': source_credit[2:]}, attrs)
        # A visible credit is editorially reviewed. Role labels and structured
        # technical-field lists are no longer valid external attributions.
        invalid = (
            '— Assistant', '— User', '— Author',
            '— Assistant · Model: gpt-6-thinking',
            '— gpt-6-thinking · Thinking effort: max',
            '— Model: gpt-6-thinking',
            '— gpt-6-thinking (max effort) · Model: forged',
            '— gpt-6-thinking <script>alert(1)</script>',
            '— ' + 'x' * 315,
            '**— gpt-6-thinking (max effort)**',
        )
        for source_credit in invalid:
            with self.subTest(credit=source_credit), self.assertRaises(AssetError):
                self.parse(reviewed(source_credit, 'assistant'))

    def test_semantic_time_is_exact_bounded_and_has_no_extra_attributes(self):
        values = {'2026-11-05': '5 Nov 2026',
                  '2026-11-05T14:37:03.123456789Z': '5 Nov 2026 · 14:37:03.123456789 UTC',
                  '2026-11-05T14:37:00-08:00': '5 Nov 2026 · 14:37 UTC-08:00'}
        for value, label in values.items():
            source = document('<a name="message"></a>\n\n## Assistant\n\n'
                '<a name="metadata"></a>\n\n*<time datetime="' + value + '">' + label + '</time> · Model: gpt-6-thinking · Thinking effort: max*')
            self.parse(source)
        for markup in ('<time datetime="2026-02-29">29 Feb 2026</time>',
                       '<time datetime="2026-11-05T14:37:00">Local time</time>',
                       '<time datetime="2026-11-05T14:37:00-00:00">Unknown zone</time>',
                       '<time datetime="2026-11-05" onclick="alert(1)">5 Nov 2026</time>',
                       '<time datetime="2026-11-05" style="color:red">5 Nov 2026</time>',
                       '<time datetime="2026-11-05">A different date</time>',
                       '<time datetime="2026-11-05"><strong>5 Nov 2026</strong></time>'):
            with self.subTest(markup=markup), self.assertRaises(AssetError):
                self.parse(document('<a name="metadata"></a>\n\n' + markup))

    def test_timestamp_precision_and_explicit_timezones_remain_authoritative(self):
        valid = {'2024-02-29':'29 Feb 2024',
            '2026-11-05T14:37:03.123456789+05:30':'5 Nov 2026 · 14:37:03.123456789 UTC+05:30',
            '2026-11-05T00:00:00+14:00':'5 Nov 2026 · 00:00 UTC+14:00',
            '2026-11-05T00:00:00-12:00':'5 Nov 2026 · 00:00 UTC-12:00'}
        for instant,label in valid.items():
            self.assertTrue(valid_timestamp(instant));self.assertEqual(label,timestamp_label(instant))
        for bad in (None,'',True,1793941631,'2026-02-29','2026-11-05T14:37:00',
                    '2026-11-05T14:37:00-00:00','2026-11-05T14:37:00+14:01',
                    '2026-11-05T14:37:00-12:01','2026-11-05T14:37:00+05:99',
                    '2026-11-05T14:37:00.1234567890Z','2026-11-05T25:37:00Z'):
            self.assertFalse(valid_timestamp(bad),str(bad))








def load_tests(loader, tests, pattern):
    return unittest.TestSuite(loader.loadTestsFromName(name,cls) for cls in (SourceContractTests,) for name in cls.__dict__ if name.startswith("test_"))
