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
from extract_chatgpt import transcript
from supporting_gfm import load_gfm_record
from validate_sources import git_sources, validate_repository
from verify_edition import verify_edition, EditionHTML
from test_assets import EditionTests, SVG
from test_extract_chatgpt import ExtractTests

ROOT = Path(__file__).resolve().parents[1]
NOTE = (ROOT/'templates/work/supporting/note.md').read_text()


class GFMSourceTests(unittest.TestCase):
    def test_narrow_source_rejects_unsafe_dangling_duplicate_and_implicit_anchors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'note.md'
            path.write_text(NOTE)
            _,anchors,_,_=load_gfm_record(path)
            self.assertEqual({'document-note','claim','calculation','code'},set(anchors))
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
    def test_import_is_deterministic_syntax_aware_and_reports_loss(self):
        text = ('A claim with \\(x=x\\) and citeturn0search0.\n\n'
                '\\[\nx=x\n\\]\n\n```python\nprint(r"\\(literal\\)")\n```\n\n'
                '| A | B |\n|---|---|\n| 1 | 2 |\n\n<img src="tracker">')
        self.record['mapping']['tip']['message']['content']['parts']=[text,{'attachment':'omitted'}]
        record=transcript(self.record,'tip')
        draft,diagnostics=review_gfm(record)
        self.assertEqual((draft,diagnostics),review_gfm(record))
        self.assertIn(b'\\(literal\\)',draft)
        self.assertIn(b'$x=x$',draft)
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
        self.assertEqual({'document-note','claim','calculation','code'},set(entry['anchors']))
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
