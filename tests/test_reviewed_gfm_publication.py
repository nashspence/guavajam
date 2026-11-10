"""Private native inputs end at review; only committed GFM enters an edition."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

from test_assets import EditionTests
from supporting_gfm import load_gfm_record
from verify_edition import verify_edition

ROOT = Path(__file__).resolve().parents[1]


class ReviewedGFMPublicationTests(EditionTests):
    def test_private_import_review_snapshot_and_publication_have_one_source_authority(self):
        archive = self.base / 'private-export.zip'
        def message(text):
            return {'parent':'root','message':{'author':{'role':'assistant'},'create_time':1793941631.125,
                'content':{'content_type':'text','parts':[text]}}}
        conversation={'conversation_id':'chosen','title':'Reviewed discussion','current_node':'selected',
            'mapping':{'root':{'parent':None,'message':None},
                       'selected':message('A **reviewed claim** with Café and Δ.\n\n<script>literal()</script>'),
                       'sibling':message('Synthetic unselected private content')}}
        with zipfile.ZipFile(archive,'w') as bundle:
            bundle.writestr('conversations-001.json',json.dumps([conversation]))
        review=self.base/'review.md'
        subprocess.run([sys.executable,ROOT/'scripts/extract_chatgpt.py',archive,
                        '--conversation','chosen','--output',review],check=True,capture_output=True)
        self.assertEqual(0o600,review.stat().st_mode&0o777)
        _,anchors,_,_=load_gfm_record(review)
        message_id=next(key for key in anchors if key.startswith('m-'))
        folder=self.repo/'works/sample/supporting';folder.mkdir()
        selected=folder/'exchange.md';shutil.copyfile(review,selected)
        digest=hashlib.sha256(selected.read_bytes()).hexdigest()
        shutil.copyfile(ROOT/'templates/work/supporting/note.md',folder/'uncited.md')
        with (self.repo/'works/sample/manuscript.md').open('a') as stream:
            stream.write('\nRead the [message](supporting/exchange.md#'+message_id+') and '
                         '[whole discussion](supporting/exchange.md).\n')
        self.git('add','works');self.git('commit','-qm','Review GFM only')
        self.commit=self.git('rev-parse','HEAD')
        selected.write_text('Unreviewed replacement')
        archive.unlink();review.unlink()
        manifest=self.build();entry,=manifest['supporting_documents']
        self.assertEqual('works/sample/supporting/exchange.md',entry['source_path'])
        self.assertEqual(digest,entry['sha256'])
        self.assertEqual(set(entry['anchors']),set(entry['elements']))
        page=(self.output/manifest['canonical_artifact']).read_text()
        self.assertIn('<strong>reviewed claim</strong>',page)
        self.assertIn('Café',page);self.assertIn('Δ',page)
        self.assertIn('&lt;script&gt;literal()&lt;/script&gt;',page)
        self.assertNotIn('<time datetime="2026-11-06T05:07:11.125Z">',page)
        self.assertIn('<figcaption class="press-turn-attribution">',page)
        for omitted in ('Synthetic unselected private content','Unreviewed replacement','uncited.md','conversations-001.json'):
            self.assertNotIn(omitted,page)
        self.assertEqual({manifest['canonical_artifact'],'provenance.json','SHA256SUMS'},
                         {path.name for path in self.output.iterdir()})
        self.assertEqual(manifest,verify_edition(self.output,commit=self.commit,tag=self.tag))


def load_tests(loader,tests,pattern):
    return loader.loadTestsFromName('test_private_import_review_snapshot_and_publication_have_one_source_authority',
                                    ReviewedGFMPublicationTests)
