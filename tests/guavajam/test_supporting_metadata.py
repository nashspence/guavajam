"""Submitter-defined metadata is visible, scoped, citable and inert."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import yaml
from unittest.mock import patch

from guavajam_test_support import FIXTURES, RESOURCES
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
from guavajam.asset_policy import AssetError
from guavajam.supporting_gfm import load_gfm_record
from guavajam.source_ast import walk
from guavajam.validate_sources import git_sources, validate_repository
from test_assets import SVG


BODY = '<a name="document"></a>\n\n<a name="claim"></a>\n\nReviewed claim.\n'
FIELDS = '''date_created: 2026-11-08
user_account: "example-user"
exported_identity: "Example User"
title: "Supporting metadata title"
schema: "A submitter label"
unknown_: "Do not discard this field"
header-includes: '<script>literal()</script>'
"Quoted <key> & label": ['first & literal', 'second <literal>']
boolean: yes
number: 001
empty: ""
multiline: |
  First line.
  Second line.
'''


def document(fields=FIELDS, *, anchor='details', body=BODY):
    return '---\ntitle: Body title\nmetadata:\n' + ''.join('  '+line+'\n' for line in fields.splitlines()) + '---\n\n<a name="' + anchor + '"></a>\n\n' + body


class MetadataSourceTests(unittest.TestCase):
    def parse(self, source):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / 'note.md'; path.write_text(source)
            return load_gfm_record(path)

    def test_free_form_labels_order_scalar_spellings_and_lists_survive_without_pandoc_semantics(self):
        ast, anchors, *_ = self.parse(document())
        self.assertEqual({}, ast['meta'])
        self.assertEqual({'document', 'details', 'claim'}, set(anchors))
        self.assertEqual(0, anchors['document']); self.assertEqual(1, anchors['details'])
        keys = [dict(n['c'][0][2])['data-guavajam-metadata-key'] for n in walk(ast['blocks'])
                if n['t'] == 'Span' and 'data-guavajam-metadata-key' in dict(n['c'][0][2])]
        self.assertEqual(['date_created','user_account','exported_identity','title','schema','unknown_',
                          'header-includes','Quoted <key> & label','boolean','number','empty','multiline'], keys)
        values = [n['c'][1][0]['c'] if n['c'][1] else '' for n in walk(ast['blocks'])
                  if n['t'] == 'Span' and 'data-guavajam-metadata-value' in dict(n['c'][0][2])]
        self.assertEqual(['2026-11-08','example-user','Example User','Supporting metadata title','A submitter label',
                          'Do not discard this field','<script>literal()</script>','first & literal','second <literal>',
                          'yes','001','','First line.\nSecond line.\n'], values)

    def test_document_identity_is_plain_metadata_separate_from_submitter_labels(self):
        from guavajam.supporting_metadata import document_frontmatter, split_frontmatter
        facts = {'title': 'Evidence <literal> & *text*', 'abstract': 'A literal <summary>.'}
        fields = [('title', 'A submitter claim'), ('abstract', 'Another submitter claim')]
        source = document_frontmatter(**facts, metadata=fields)
        parsed, metadata, body = split_frontmatter(source + BODY)
        self.assertEqual(facts, parsed)
        self.assertEqual(fields, metadata)
        self.assertEqual(BODY, body)
        ast, anchors, *_ = self.parse(source + '<a name="details"></a>\n\n' + BODY)
        self.assertEqual(facts['title'], ast['blocks'][0]['c'][2][0]['c'])
        self.assertEqual({'document', 'details', 'claim'}, set(anchors))

    def test_reviewed_title_and_optional_abstract_are_bounded_and_unambiguous(self):
        from guavajam.supporting_metadata import document_frontmatter
        for fields in ('', 'abstract: Summary\n', 'title: ""\n', 'title: true\n',
                       'title: One\ntitle: Two\n', 'title: One\nabstract: A\nabstract: B\n',
                       'title: One\nunknown: ignored\n', 'title: One\nmetadata: []\n',
                       'title: "line\\nbreak"\n', 'title: One\nabstract: "line\\nbreak"\n',
                       'title: "' + 'x' * 4097 + '"\n',
                       'title: One\nabstract: "' + 'é' * 2049 + '"\n'):
            with self.subTest(fields=fields[:40]), self.assertRaises(AssetError):
                self.parse('---\n' + fields + '---\n\n' + BODY)
        self.parse(document_frontmatter('One', abstract='é' * 2048) + BODY)
        with self.assertRaisesRegex(AssetError, 'H2–H5'):
            self.parse(document_frontmatter('One') + BODY + '\n<a name="repeat"></a>\n\n# Repeated title\n')

    def test_metadata_has_its_own_explicit_composite_anchor(self):
        for source in (document(anchor='claim'), document(anchor='document'),
                       '---\ntitle: Body title\nmetadata: {key: value}\n---\n\n' + BODY,
                       document().replace('<a name="details"></a>', '<a name="Bad_ID"></a>')):
            with self.subTest(source=source[:50]), self.assertRaisesRegex(AssetError, 'anchor'):
                self.parse(source)
        self.parse(document(body=BODY + '\n<a name="return"></a>\n\n[Metadata](#details).\n'))

    def test_free_form_metadata_is_optional_with_a_reviewed_document_title(self):
        for source in ('---\ntitle: Body title\n---\n\n' + BODY,
                       '---\ntitle: Body title\nmetadata: {}\n---\n\n' + BODY):
            ast, anchors, *_ = self.parse(source)
            self.assertEqual({'document','claim'}, set(anchors))
            self.assertFalse(any(n['t'] == 'DefinitionList' for n in walk(ast['blocks'])))

    def test_metadata_keeps_source_anchor_and_literal_author_assertions(self):
        ast,anchors,*_=self.parse(document('Source: A submitter claim\nStatus: In review\n'))
        self.assertIn('details',anchors)
        metadata=ast['blocks'][anchors['details']]
        self.assertIn('guavajam-document-metadata',metadata['c'][0][1])
        self.assertIn('A submitter claim',str(metadata))


    def test_dense_metadata_retains_64_ordered_fields_and_no_implicit_semantics(self):
        source = document(''.join(f'field-{n:02d}: "Value {n:02d} — Café"\n' for n in range(64)))
        ast, anchors, *_ = self.parse(source)
        self.assertEqual({}, ast['meta'])
        keys = [dict(node['c'][0][2])['data-guavajam-metadata-key'] for node in walk(ast['blocks'])
                if node['t'] == 'Span' and 'data-guavajam-metadata-key' in dict(node['c'][0][2])]
        self.assertEqual([f'field-{n:02d}' for n in range(64)], keys)
        self.assertEqual({'document', 'details', 'claim'}, set(anchors))

    def test_unsafe_ambiguous_or_complex_yaml_is_rejected_without_dumping_values(self):
        invalid = ('a: first\na: second\n', 'a: &value private-sentinel\nb: *value\n',
                   'a: !!str private-sentinel\n', 'a:\n  nested: private-sentinel\n',
                   'a: [[private-sentinel]]\n', '- private-sentinel\n',
                   '? [a,b]\n: private-sentinel\n', '"": private-sentinel\n',
                   '"line\\nbreak": private-sentinel\n', 'a: "unterminated private-sentinel\n')
        for fields in invalid:
            with self.subTest(fields=fields[:15]), self.assertRaises(AssetError) as caught:
                self.parse(document(fields))
            self.assertNotIn('private-sentinel', str(caught.exception))
        with self.assertRaisesRegex(AssetError, 'metadata'):
            self.parse(document('key: "' + 'x' * (64 * 1024) + '"\n'))
        with self.assertRaises(AssetError):
            self.parse(document('---\na: value\n'))
        with self.assertRaisesRegex(AssetError, '64 fields'):
            self.parse(document(''.join(f'field{n}: value\n' for n in range(65))))

    def test_metadata_markers_cannot_be_forged_in_authored_html_and_later_yaml_is_not_silently_consumed(self):
        for markup in ('<span data-guavajam-metadata-key="forged">Text</span>',
                       '<div class="guavajam-document-metadata">Text</div>'):
            with self.assertRaises(AssetError):
                self.parse(document(body=BODY + '\n<a name="forged"></a>\n\n' + markup + '\n'))
        later = document(body=BODY) + '\n---\nunknown_: must remain visible or fail\n---\n'
        with self.assertRaises(AssetError): self.parse(later)

    def test_metadata_is_optional_local_import_input_and_never_changes_default_output(self):
        native = json.loads((FIXTURES / 'capture/conversation.json').read_text())
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch); source = root/'capture.json'; source.write_text(json.dumps([native]))
            metadata = root/'optional fields.yml'; metadata.write_text(FIELDS)
            output = root/'review.md'
            command = ['make','--no-print-directory','chat-gfm',f'PYTHON={sys.executable}',
                       'FILE='+str(source),'CONVERSATION='+native['conversation_id'],'OUTPUT='+str(output),
                       'METADATA='+str(metadata)]
            run = subprocess.run(command,cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(0,run.returncode,run.stderr)
            self.assertEqual(0o600,output.stat().st_mode&0o777)
            ast, anchors, *_ = load_gfm_record(output)
            self.assertEqual(1,sum(n['t']=='DefinitionList' for n in walk(ast['blocks'])))
            self.assertTrue(any(key.startswith('metadata-doc-') for key in anchors))
            self.assertNotIn('Example User',run.stdout+run.stderr)
            self.assertNotEqual(0,subprocess.run(command,cwd=ROOT,capture_output=True).returncode)
            metadata.write_text('a: &private private-sentinel\nb: *private\n')
            output.unlink()
            bad = subprocess.run(command,cwd=ROOT,capture_output=True,text=True)
            self.assertNotEqual(0,bad.returncode); self.assertFalse(output.exists())
            self.assertNotIn('private-sentinel',bad.stdout+bad.stderr)






def load_tests(loader, tests, pattern):
    return unittest.TestSuite(loader.loadTestsFromName(name,cls) for cls in (MetadataSourceTests,) for name in cls.__dict__ if name.startswith("test_"))
