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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_policy import AssetError, verify_bytes
from supporting_gfm import load_gfm_record, timestamp_label, valid_timestamp
from render_profile import plain_text, walk
from validate_sources import git_sources, validate_repository
from verify_edition import verify_edition
from manage_assets import upload
from test_assets import EditionTests


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

    def test_speaker_is_explicit_on_message_anchor_not_inferred_from_heading(self):
        source = document('<a name="plain"></a>\n\n## Assistant\n\n'
            '<a name="author-entry" data-press-speaker="author"></a>\n\n## Author\n\n'
            '<a name="responder-entry" data-press-speaker="responder"></a>\n\n## Correspondent\n\n'
            '<a name="body"></a>\n\nActual source text is preserved.\n')
        ast, anchors, *_ = self.parse(source)
        self.assertEqual({'document','plain','author-entry','responder-entry','body'}, set(anchors))
        headings = [n for n in ast['blocks'] if n['t'] == 'Header']
        self.assertEqual([None,'author','responder'], [dict(h['c'][1][2]).get('data-press-speaker')
                         for h in headings[1:]])
        # A title that says 'Assistant' alone is *not* a sourced attribution.
        for tag in ('user', 'assistant'):
            self.parse(document('<a name="named" data-press-speaker="'+tag+'"></a>\n\n## Any label\n'))
        for bad in (
            '<a name="msg" data-press-speaker="unknown"></a>\n\n## Respondent\n',
            '<a name="msg" data-press-speaker="Assistant"></a>\n\n## Respondent\n',
            '<a name="msg" data-press-speaker="responder" style="color:red"></a>\n\n## Respondent\n',
            '<a name="msg" data-press-speaker="responder"></a>\n\n### Respondent\n',
            '<a name="msg" data-press-speaker="responder"></a>\n\nA paragraph.\n',
            '<a name="msg" data-press-speaker="responder"></a>\n\n',
            '<a name="msg" data-press-speaker="responder"></a>\n\n<details>raw</details>\n',
        ):
            with self.subTest(source=bad[:70]), self.assertRaises(AssetError):
                self.parse(document(bad))
        with self.assertRaisesRegex(AssetError, 'unannotated'):
            self.parse('---\nDescription: Testing\n---\n\n'
                       '<a name="metadata" data-press-speaker="author"></a>\n\n<a name="doc"></a>\n\n# Evidence\n')

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


class CustodyTests(EditionTests):
    def test_local_review_output_is_gfm_only_and_never_writes_a_json_source(self):
        source=self.base/'private-native.json'
        native=json.loads((ROOT/'tests/fixtures/capture/conversation.json').read_text())
        source.write_text(json.dumps([native]))
        output=self.base/'review.json'
        result=subprocess.run([sys.executable,ROOT/'scripts/extract_chatgpt.py',source,
            '--conversation',native['conversation_id'],'--output',output],capture_output=True,text=True)
        self.assertNotEqual(0,result.returncode)
        self.assertIn('GFM .md',result.stderr)
        self.assertFalse(output.exists())

    def test_json_supporting_documents_and_their_references_are_rejected(self):
        folder = self.repo / 'works/sample/supporting'; folder.mkdir()
        path = folder / 'note.json'
        path.write_text(json.dumps({'title':'Old route','scope':'Reviewed','passages':[
            {'id':'claim','label':'User','text':'Text'}]}))
        self.git('add','works')
        self.assertTrue(validate_repository(self.repo,git_sources(self.repo)))
        with (self.repo / 'works/sample/manuscript.md').open('a') as stream:
            stream.write('\n[Unsupported evidence](supporting/note.json#claim)\n')
        self.assertTrue(validate_repository(self.repo,git_sources(self.repo)))

    def test_native_conversation_json_is_not_a_tracked_source_or_opaque_asset(self):
        raw = (ROOT / 'tests/fixtures/capture/conversation.json').read_bytes()
        for name in ('conversations.json','renamed.bin','work.zip'):
            data=raw
            if name.endswith('.zip'):
                output=io.BytesIO()
                with zipfile.ZipFile(output,'w') as archive: archive.writestr('conversations.json',raw)
                data=output.getvalue()
            path=self.repo/name;path.write_bytes(data);self.git('add',name)
            with self.subTest(name=name):
                self.assertTrue(any('native' in error.lower() for error in validate_repository(self.repo,git_sources(self.repo))))
                with self.assertRaisesRegex(AssetError,'native'):
                    verify_bytes(data,'bin','application/octet-stream')
                store=Mock()
                with self.assertRaisesRegex(AssetError,'native'):
                    upload(self.repo,path,store)
                store.upload.assert_not_called()
            self.git('rm','-f',name)
        self.assertEqual('application/octet-stream',verify_bytes(b'{"measurements":[1,2,3]}','bin','application/octet-stream'))
        ordinary=io.BytesIO()
        with zipfile.ZipFile(ordinary,'w') as archive:
            archive.writestr('conversations.json','{"measurements":[1,2,3]}')
        self.assertEqual('application/octet-stream',verify_bytes(ordinary.getvalue(),'bin','application/octet-stream'))
        self.assertEqual([],validate_repository(self.repo,git_sources(self.repo)))


class ElementPublicationTests(EditionTests):
    def source(self):
        folder=self.repo/'works/sample/supporting';folder.mkdir()
        text=document('<a name="message-one"></a>\n\n## Assistant\n\n'
            '<a name="metadata-one"></a>\n\n*<time datetime="2026-11-05">5 Nov 2026</time> · Model: gpt-6-thinking · Thinking effort: max*\n\n'
            '<a name="prose"></a>\n\nA **reviewed** claim.\n\n'
            '<a name="equation"></a>\n\n$$\nx=x\n$$\n\n'
            '<a name="table"></a>\n\n| A |\n|---|\n| 1 |\n\n'
            '<a name="program"></a>\n\n```python\nprint("literal <tag>")\n```\n\n'
            '<a name="list"></a>\n\n- First\n  - Nested\n\n'
            '<a name="message-two"></a>\n\n## Assistant\n\n'
            '<a name="reply"></a>\n\nA different response.\n')
        (folder/'note.md').write_text(text)
        with (self.repo/'works/sample/manuscript.md').open('a') as stream:
            stream.write('\nSee '+', '.join('[the '+key+'](supporting/note.md#'+key+')' for key in
                         ('message-one','message-two','prose','equation','table','program','list'))+'.\n')
        self.git('add','works');self.git('commit','-qm','Review citable GFM evidence')
        self.commit=self.git('rev-parse','HEAD')

    def rewrite(self, raw, manifest):
        manifest.update(html_sha256=hashlib.sha256(raw).hexdigest(),html_byte_count=len(raw))
        (self.output/manifest['canonical_artifact']).write_bytes(raw)
        (self.output/'provenance.json').write_text(json.dumps(manifest))
        (self.output/'SHA256SUMS').write_text(''.join(hashlib.sha256((self.output/n).read_bytes()).hexdigest()+'  '+n+'\n'
            for n in (manifest['canonical_artifact'],'provenance.json')))

    def test_each_citation_is_bound_to_its_exact_published_element(self):
        self.source();manifest=self.build();entry,=manifest['supporting_documents']
        self.assertEqual(set(entry['anchors']),set(entry['elements']))
        raw=(self.output/manifest['canonical_artifact']).read_bytes()
        self.assertIn(b'class="press-passage-heading"',raw)
        self.assertIn(b'class="press-passage-datetime"',raw)
        self.assertIn(b'<time datetime="2026-11-05">5 Nov 2026</time>',raw)
        self.assertEqual(manifest,verify_edition(self.output))
        # Swap all IDs, citation links and permalink metadata coherently. Merely
        # checking that each ID and link exists would accept the wrong targets.
        a,b=(entry['anchors'][key].encode() for key in ('prose','program'))
        swapped=raw.replace(a,b'press-swap-placeholder').replace(b,a).replace(b'press-swap-placeholder',b)
        changed=copy.deepcopy(manifest);self.rewrite(swapped,changed)
        with self.assertRaisesRegex(AssetError,'element'):
            verify_edition(self.output)

    def test_explicit_speaker_survives_publication_and_fingerprint_verification(self):
        folder = self.repo / 'works/sample/supporting'; folder.mkdir()
        source = document('<a name="user-turn" data-press-speaker="author"></a>\n\n## Author\n\n'
            '<a name="user-text"></a>\n\nAn attributed question.\n\n'
            '<a name="reply" data-press-speaker="responder"></a>\n\n## Responder\n\n'
            '<a name="reply-text"></a>\n\nAn attributed answer.\n\n'
            '<a name="plain"></a>\n\n## Assistant\n\n'
            '<a name="plain-text"></a>\n\nUnattributed heading remains neutral.')
        (folder / 'voices.md').write_text(source)
        with (self.repo / 'works/sample/manuscript.md').open('a') as stream:
            stream.write('\n[Question](supporting/voices.md#user-turn), '
                         '[answer](supporting/voices.md#reply), '
                         '[neutral](supporting/voices.md#plain).\n')
        self.git('add', 'works'); self.git('commit', '-qm', 'Review explicit source speaker roles')
        self.commit = self.git('rev-parse', 'HEAD')
        manifest = self.build()
        entry, = manifest['supporting_documents']
        html = (self.output / manifest['canonical_artifact']).read_bytes()
        self.assertIn(b'data-press-speaker="author"', html)
        self.assertIn(b'data-press-speaker="responder"', html)
        self.assertNotIn(b'data-press-speaker="assistant"', html)
        self.assertIn('user-turn', entry['anchors']); self.assertIn('reply', entry['anchors'])
        self.assertEqual(manifest, verify_edition(self.output))
        altered = html.replace(b'data-press-speaker="responder"', b'data-press-speaker="author"')
        self.assertNotEqual(html, altered)
        changed = copy.deepcopy(manifest); self.rewrite(altered, changed)
        with self.assertRaisesRegex(AssetError, 'element'):
            verify_edition(self.output)

    def test_dashed_document_and_fragment_names_have_distinct_namespaces(self):
        folder=self.repo/'works/sample/supporting';folder.mkdir()
        for name,key,text in (('a-b','c','First document claim.'),('a','b-c','Second document claim.')):
            (folder/(name+'.md')).write_text(document('<a name="'+key+'"></a>\n\n'+text))
        with (self.repo/'works/sample/manuscript.md').open('a') as stream:
            stream.write('\n[First](supporting/a-b.md#c) and [Second](supporting/a.md#b-c).\n')
        self.git('add','works');self.git('commit','-qm','Review distinct dashed namespaces')
        self.commit=self.git('rev-parse','HEAD')
        manifest=self.build()
        a,b=manifest['supporting_documents']
        self.assertEqual('support-md-3-a-b-c',a['anchors']['c'])
        self.assertEqual('support-md-1-a-b-c',b['anchors']['b-c'])
        self.assertNotEqual(a['anchors']['c'],b['anchors']['b-c'])
        self.assertEqual(manifest,verify_edition(self.output))

    def test_element_inventory_and_nested_composite_content_cannot_be_dropped_or_changed(self):
        self.source();manifest=self.build()
        raw=(self.output/manifest['canonical_artifact']).read_bytes()
        for edit in ('inventory','nested'):
            changed=copy.deepcopy(manifest)
            updated=raw
            if edit=='inventory': changed['supporting_documents'][0]['elements'].pop('list')
            else:
                updated=raw.replace(b'>Nested<',b'>Substituted<')
                self.assertNotEqual(raw,updated)
            self.rewrite(updated,changed)
            with self.assertRaisesRegex(AssetError,'element'):
                verify_edition(self.output)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(loader.loadTestsFromName(name,cls) for cls in
        (SourceContractTests,CustodyTests,ElementPublicationTests)
        for name in cls.__dict__ if name.startswith('test_'))
