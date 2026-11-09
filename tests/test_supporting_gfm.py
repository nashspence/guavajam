"""The reviewed Markdown and its explicit block anchors are publication source."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from asset_policy import AssetError
from build_edition import build_edition
from chat_gfm import review_gfm
from render_profile import walk
from extract_chatgpt import transcript
from supporting_gfm import load_gfm_record
from validate_sources import git_sources, validate_repository
from verify_edition import verify_edition, EditionHTML
from test_assets import EditionTests, SVG
from test_extract_chatgpt import ExtractTests

ROOT = Path(__file__).resolve().parents[1]
NOTE = (ROOT/'templates/work/supporting/note.md').read_text()


class GFMSourceTests(unittest.TestCase):
    def test_tooltips_are_balanced_title_only_literal_data(self):
        source = NOTE + ('\n<a name="tooltip-text"></a>\n\n<span title=\'&lt;text color="secondary"&gt;\'>Readable **text**</span>\n\n'
                         '<a name="tooltip-icon"></a>\n\n<span title="&lt;icon name=&quot;alert-triangle&quot;/&gt;">□</span>\n')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'note.md'; path.write_text(source)
            ast, *_ = load_gfm_record(path)
            spans = [node for node in walk(ast['blocks']) if node['t'] == 'Span' and 'title' in dict(node['c'][0][2])]
            self.assertEqual(['<text color="secondary">', '<icon name="alert-triangle"/>'],
                             [dict(node['c'][0][2])['title'] for node in spans])
            for markup in ('<span title="x" onclick="alert(1)">Text</span>',
                           '<span title="x" style="display:none">Text</span>',
                           '<span title="x" id="claim">Text</span>',
                           '<span title="x"><script>alert(1)</script></span>',
                           '<span title="x"><img src="https://example.org/tracker"></span>',
                           '<span title="x">Unclosed',
                           'Unopened</span>',
                           '<span title="&#10;">Line break</span>'):
                with self.subTest(markup=markup):
                    path.write_text(NOTE + '\n' + markup + '\n')
                    with self.assertRaises(AssetError):
                        load_gfm_record(path)

    def test_narrow_source_rejects_unsafe_dangling_duplicate_and_implicit_anchors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'note.md'
            path.write_text(NOTE)
            _,anchors,_,_=load_gfm_record(path)
            self.assertEqual({'document-note','document-metadata','scope','claim','calculation','code','return'},set(anchors))
            for bad in [
                NOTE+'\n<script>alert(1)</script>\n', NOTE.replace('#claim','#missing'),
                NOTE.replace('#claim','../manuscript.md'), NOTE.replace('#claim','javascript:alert(1)'),
                NOTE.replace('name="code"','name="claim"'), NOTE+'\n<a name="dangling"></a>\n',
                NOTE.replace('<a name="claim"></a>','<a name="Bad_ID"></a>'),
                NOTE.replace('<a name="claim"></a>','<div id="claim"></div>'),
                NOTE.replace('#claim','#reviewed-supporting-note'),
                NOTE.replace('<a name="claim"></a>','Inline <a name="claim"></a> text.'),
                NOTE.replace('name="document-note"','name="reviewed-supporting-note"'),
            ]:
                with self.subTest(bad=bad[-100:]):
                    path.write_text(bad)
                    with self.assertRaises((AssetError,ValueError)): load_gfm_record(path)
            path.unlink();path.symlink_to(ROOT/'templates/work/supporting/note.md')
            with self.assertRaises(AssetError):load_gfm_record(path)


class GFMImportTests(ExtractTests):
    def test_attribution_is_a_subtitle_and_missing_metadata_is_not_invented(self):
        msg = self.record['mapping']['tip']['message']
        msg.update(create_time=1577836800.125)
        msg['metadata'].update(model_slug='gpt-6-thinking', thinking_effort='max')
        data, _ = review_gfm(transcript(self.record, 'tip'))
        self.assertIn(b'## Assistant\n', data)
        self.assertIn(b'<time datetime="2020-01-01T00:00:00.125Z">1 Jan 2020', data)
        self.assertIn(' · Model: gpt-6-thinking · Thinking effort: max*'.encode(), data)
        self.assertIn(b'## User\n\n', data)
        self.assertNotIn(b'## Assistant (', data)
        self.assertNotIn(b'Pro', data)
        msg.pop('create_time')
        msg['metadata'].clear()
        data, _ = review_gfm(transcript(self.record, 'tip'))
        self.assertNotIn(b'Model:', data)
        self.assertNotIn(b'Thinking effort:', data)
        self.assertNotIn(b'2020-', data)

    def test_review_gfm_keeps_exported_speaker_without_guessing_a_heading(self):
        record = transcript(self.record, 'tip')
        source, _ = review_gfm(record)
        self.assertIn(b'data-press-speaker="user"', source)
        self.assertIn(b'data-press-speaker="assistant"', source)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'review.md'; path.write_bytes(source)
            ast, anchors, *_ = load_gfm_record(path)
        self.assertEqual(['user', 'assistant'], [dict(n['c'][1][2])['data-press-speaker']
            for n in ast['blocks'] if n['t'] == 'Header' and n['c'][0] == 2])
        self.assertTrue(all(key in anchors for key in [p['id'] for p in record['passages']]))
        # Reviewed labels and source roles are independent. Absence means neutral.
        alternate = copy.deepcopy(record)
        alternate['passages'][0]['label'] = 'Assistant'
        alternate['passages'][0]['speaker'] = 'user'
        alternate['passages'][1].pop('speaker')
        text, _ = review_gfm(alternate)
        self.assertEqual(text.count(b'data-press-speaker='), 1)
        for value in ({'forged': True}, 'system', 'ASSISTANT', '', 2, ['user']):
            bad = copy.deepcopy(record); bad['passages'][0]['speaker'] = value
            with self.subTest(value=str(value)), self.assertRaises(AssetError):
                review_gfm(bad)

    def test_dividers_split_nested_wrappers_and_lists_but_remain_literal_in_code(self):
        tag = '<divider color="subtle"/>'
        text = ('<box gap={3}><text>Before</text>' + tag + '<text>After</text></box>\n\n'
                + tag + '\n\n- First ' + tag + ' second\n\n'
                '| Cell |\n|---|\n| Before ' + tag + ' after |\n\n'
                '# Heading ' + tag + ' end\n\n'
                'Literal ' + chr(96) + tag + chr(96) + '\n\n'
                + chr(96) * 3 + '\n' + tag + '\n' + chr(96) * 3 + '\n\n'
                '    ' + tag + '\n')
        self.record['mapping']['tip']['message']['content']['parts'] = [text]
        data, diagnostics = review_gfm(transcript(self.record, 'tip'))
        self.assertEqual(5, diagnostics['chatgpt-divider-normalized'])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'review.md'; path.write_bytes(data)
            ast, *_ = load_gfm_record(path)
        nodes = list(walk(ast['blocks']))
        self.assertEqual(3, sum(node['t'] == 'HorizontalRule' for node in nodes))
        self.assertEqual(3, [node['c'][1] for node in nodes if node['t'] in {'Code', 'CodeBlock'}].count(tag))
        divider = [node for node in nodes if node['t'] == 'Span' and dict(node['c'][0][2]).get('title') == tag]
        self.assertEqual(2, len(divider), 'headings and table cells use a quiet separator with the exact tag on hover')
        self.assertEqual('—', divider[0]['c'][1][0]['c'])
        self.assertTrue(all(not any(node['t'] == 'HorizontalRule' for node in walk(span['c'][1]))
                            for span in nodes if span['t'] == 'Span'))

    def test_presentation_wrappers_and_icon_placeholders_preserve_exact_hover_data(self):
        opening = '<text color="secondary" weight="medium" size="sm">'
        icon = '<icon name="alert-triangle" color="danger" inline/>'
        text = opening + 'Technical **Notebook**</text> and ' + icon + '\n'
        self.record['mapping']['tip']['message']['content']['parts'] = [text]
        data, diagnostics = review_gfm(transcript(self.record, 'tip'))
        self.assertEqual(1, diagnostics['presentation-wrapper-retained-in-tooltip'])
        self.assertEqual(1, diagnostics['unsupported-icon-retained-in-tooltip'])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'review.md'; path.write_bytes(data)
            ast, *_ = load_gfm_record(path)
        spans = [node for node in walk(ast['blocks']) if node['t'] == 'Span' and 'title' in dict(node['c'][0][2])]
        self.assertEqual([opening + ' … </text>', icon], [dict(node['c'][0][2])['title'] for node in spans])
        self.assertIn('□'.encode(), data)
        self.assertFalse(any(node['t'] in {'RawInline', 'RawBlock', 'Code'} for node in walk(ast['blocks'])))

    def test_import_is_deterministic_syntax_aware_and_reports_loss(self):
        text = ('A claim with \\(x=x\\) and citeturn0search0.\n\n'
                '\\[\nx=x\n\\]\n\n```python\nprint(r"\\(literal\\)")\n```\n\n'
                '| A | B |\n|---|---|\n| 1 | 2 |\n\n<img src="tracker">')
        self.record['mapping']['tip']['message']['content']['parts']=[text,{'attachment':'omitted'}]
        record=transcript(self.record,'tip')
        draft,diagnostics=review_gfm(record)
        self.assertEqual((draft,diagnostics),review_gfm(record))
        self.assertIn(b'\\(literal\\)',draft)
        self.assertIn(b'$`x=x`$',draft)
        self.assertIn('unresolved-chatgpt-citation',diagnostics)
        self.assertIn('raw-markup-as-literal-code',diagnostics)
        self.assertIn('unsupported-message-content-omitted',diagnostics)
        self.assertNotIn(b'unselected sibling',draft)
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'review.md';p.write_bytes(draft)
            _,anchors,_,_=load_gfm_record(p)
            changed=copy.deepcopy(record)
            changed['passages'][-1]['text']='An inserted paragraph.\n\n'+record['passages'][-1]['text']
            p.write_bytes(review_gfm(changed)[0])
            _,later,_,_=load_gfm_record(p)
            self.assertLessEqual(set(anchors),set(later))

    def test_real_make_import_writes_new_private_external_review_without_committing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'export.zip';out=root/'review.md'
            with zipfile.ZipFile(source,'w') as z:
                z.writestr('conversations.json',json.dumps([self.record]))
            command=['make','chat-gfm','FILE='+str(source),'CONVERSATION=selected','OUTPUT='+str(out)]
            run=subprocess.run(command,cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(0,run.returncode,run.stderr)
            self.assertIn('selection-omissions',run.stderr)
            self.assertEqual(0o600,out.stat().st_mode&0o777)
            raw=out.read_bytes()
            self.assertNotEqual(0,subprocess.run(command,cwd=ROOT,capture_output=True).returncode)
            self.assertEqual(raw,out.read_bytes())
            self.assertNotIn(b'private metadata',raw)
            load_gfm_record(out)


class GFMPublicationTests(EditionTests):
    def source(self):
        directory=self.repo/'works/sample/supporting';directory.mkdir()
        (directory/'note.md').write_text(NOTE)
        with (self.repo/'works/sample/manuscript.md').open('a') as f:
            f.write('\nSee the [claim](supporting/note.md#claim), [math](supporting/note.md#calculation), '
                    '[code](supporting/note.md#code), and [document](supporting/note.md).\n')
        self.git('add','works');self.git('commit','-qm','Review supporting GFM')
        self.commit=self.git('rev-parse','HEAD')
        return directory/'note.md'

    def test_exact_snapshot_distinct_passages_single_appendix_and_repeatable_package(self):
        path=self.source()
        self.assertEqual([],validate_repository(self.repo,git_sources(self.repo)))
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        path.write_text('Unreviewed replacement')
        manifest=self.build()
        entry,=manifest['supporting_documents']
        self.assertEqual(digest,entry['sha256'])
        self.assertEqual({'document-note','document-metadata','scope','claim','calculation','code','return'},set(entry['anchors']))
        page=(self.output/manifest['canonical_artifact']).read_text()
        parser=EditionHTML(manifest['presentation_profile']);parser.feed(page)
        self.assertEqual(1,' '.join(page.split()).count('Supporting record — Reviewed supporting note'))
        for key in ('claim','calculation','code'):
            anchor=entry['anchors'][key]
            self.assertIn(anchor,parser.fragment_links)
            self.assertIn((anchor,manifest['record_url']+'/read#'+anchor),parser.passage_permalinks)
        self.assertIn('<math',page);self.assertIn('<pre',page);self.assertIn('identity',page)
        self.assertNotIn('Unreviewed replacement',page)
        other=self.base/'repeat'
        build_edition(self.repo,self.commit,self.tag,other,fetcher=lambda *_:(SVG,'image/svg+xml'))
        for p in self.output.iterdir():self.assertEqual(p.read_bytes(),(other/p.name).read_bytes())
        self.assertEqual(manifest,verify_edition(self.output))

    def test_namespace_collision_and_missing_fragment_fail_before_issuance(self):
        self.source()
        manuscript=self.repo/'works/sample/manuscript.md'
        with manuscript.open('a') as f:f.write('\n<a id="support-md-4-note-claim"></a>\n## Collision\n')
        self.git('add','works');self.git('commit','-qm','Invalid collision')
        self.commit=self.git('rev-parse','HEAD')
        self.assertTrue(any('duplicate' in e for e in validate_repository(self.repo,git_sources(self.repo))))
        with self.assertRaises(AssetError):self.build()
        manuscript.write_text(manuscript.read_text().replace('#claim','#missing'))
        self.assertTrue(validate_repository(self.repo,git_sources(self.repo)))

    def test_offline_verifier_rejects_retargeted_passage_or_forged_source(self):
        self.source();manifest=self.build()
        filename=manifest['canonical_artifact'];original=(self.output/filename).read_bytes()
        for edit in ('link','source'):
            changed=copy.deepcopy(manifest)
            raw=original
            if edit=='link':
                raw=raw.replace((manifest['record_url']+'/read#support-md-4-note-claim').encode(),
                                (manifest['record_url']+'/read#support-md-4-note-code').encode())
            else:changed['supporting_documents'][0]['sha256']='0'*64
            changed.update(html_sha256=hashlib.sha256(raw).hexdigest(),html_byte_count=len(raw))
            (self.output/filename).write_bytes(raw)
            (self.output/'provenance.json').write_text(json.dumps(changed))
            (self.output/'SHA256SUMS').write_text(''.join(hashlib.sha256((self.output/n).read_bytes()).hexdigest()+'  '+n+'\n' for n in (filename,'provenance.json')))
            with self.assertRaises(AssetError):verify_edition(self.output)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(loader.loadTestsFromName(name,cls) for cls in (
        GFMSourceTests,GFMImportTests,GFMPublicationTests) for name in cls.__dict__ if name.startswith('test_'))
