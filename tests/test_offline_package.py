"""Complete archival downloads, frozen formats and pre-staging expansion budgets."""
import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from asset_policy import AssetError,base64_size
import edition_package as package
from issuance import authorization,check_authorization
from verify_edition import verify_edition
from verify_source_assets import verify_source_assets
from test_asset_downloads import DownloadPublicationTests,RAW,KEY,DOWNLOAD
from test_assets import SVG,URL,ORIGIN

ROOT=Path(__file__).resolve().parents[1]


def write_zip(path,content):
    with zipfile.ZipFile(path,'w') as archive:
        for name,raw in sorted(content.items()):
            info=zipfile.ZipInfo(name);info.create_system=3;info.external_attr=0o100644<<16
            archive.writestr(info,raw)


class OfflinePackageTests(DownloadPublicationTests):
    def current(self):
        self.source();manifest=self.build_downloads()
        return manifest,self.output/manifest['supplementary_package']['filename']

    def test_single_zip_verifies_and_matches_independently_retained_commitment(self):
        manifest,archive=self.current();receipt=authorization(self.output,manifest)
        retained=self.base/'trusted.json';retained.write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n')
        saved=self.base/archive.name;shutil.copyfile(archive,saved)
        original=(self.output/manifest['canonical_artifact']).read_bytes()
        shutil.rmtree(self.output)
        with patch('socket.create_connection',side_effect=AssertionError('offline')):
            self.assertEqual(manifest,verify_edition(saved))
            with package.edition_directory(saved) as expanded:
                self.assertEqual(original,(expanded/manifest['canonical_artifact']).read_bytes())
                self.assertEqual(RAW,(expanded/KEY).read_bytes())
                self.assertEqual(manifest,verify_edition(expanded))
                check_authorization(receipt,expanded,manifest)
        run=subprocess.run(['make','verify','DIR='+str(saved),'REGISTRATION='+str(retained)],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(0,run.returncode,run.stderr)
        self.assertIn('matches the pending authorization',run.stdout)
        receipt['provenance_sha256']='0'*64;retained.write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n')
        run=subprocess.run(['make','verify','DIR='+str(saved),'REGISTRATION='+str(retained)],cwd=ROOT,capture_output=True,text=True)
        self.assertNotEqual(0,run.returncode)

    def test_complete_zip_rejects_missing_or_changed_required_bytes(self):
        manifest,archive=self.current()
        with zipfile.ZipFile(archive) as z:original={n:z.read(n) for n in z.namelist()}
        for name in original:
            for change in ('missing','changed'):
                with self.subTest(name=name,change=change):
                    content=original.copy()
                    if change=='missing':del content[name]
                    else:content[name]=b'x'+content[name][1:]
                    target=self.base/'changed.zip';write_zip(target,content)
                    with self.assertRaises((AssetError,ValueError,KeyError,TypeError)):verify_edition(target)

    def test_extracted_archive_rejects_missing_extra_and_symlinked_files(self):
        manifest,archive=self.current()
        with package.edition_directory(archive) as expanded:
            original=(expanded/KEY).read_bytes();(expanded/KEY).unlink()
            with self.assertRaises(AssetError):verify_edition(expanded)
            (expanded/KEY).write_bytes(original);(expanded/'stray.txt').write_text('unexpected')
            with self.assertRaises(AssetError):verify_edition(expanded)
            (expanded/'stray.txt').unlink();(expanded/KEY).unlink()
            outside=self.base/'outside';outside.write_bytes(original);(expanded/KEY).symlink_to(outside)
            with self.assertRaises(AssetError):verify_edition(expanded)

    def test_rehashed_offline_copy_cannot_change_reviewed_reading_content(self):
        manifest,archive=self.current()
        with zipfile.ZipFile(archive) as z:content={n:z.read(n) for n in z.namelist()}
        content['index.html']+=b'<p>Unreviewed text</p>'
        manifest['supplementary_package'].update(reading_copy_sha256=hashlib.sha256(content['index.html']).hexdigest(),reading_copy_byte_count=len(content['index.html']))
        content['provenance.json']=json.dumps(manifest).encode();content['SHA256SUMS']=package.checksums(manifest,content)
        target=self.base/'self-consistent.zip';write_zip(target,content)
        with self.assertRaisesRegex(AssetError,'canonical derivation'):verify_edition(target)

    def test_transport_metadata_does_not_change_the_edition_payload_identity(self):
        manifest,archive=self.current();receipt=authorization(self.output,manifest)
        target=self.base/'repacked.zip';shutil.copyfile(archive,target)
        with zipfile.ZipFile(target,'a') as z:z.comment=b'Different transport metadata; identical issued contents'
        self.assertNotEqual(archive.read_bytes(),target.read_bytes())
        self.assertEqual(manifest,verify_edition(target))
        with package.edition_directory(target) as expanded:check_authorization(receipt,expanded,manifest)


class OfflineHistoryTests(unittest.TestCase):
    def test_archived_three_file_and_supplemental_v1_fixtures_keep_verifying(self):
        for name in ('archived-v1','supplemental-v1','supplemental-v2'):
            with self.subTest(name=name),patch('socket.create_connection',side_effect=AssertionError('offline')):
                manifest=verify_edition(ROOT/'tests/fixtures'/name)
                with patch('edition_package.CURRENT_FORMAT','press.offline.v3'):
                    self.assertEqual(manifest,verify_edition(ROOT/'tests/fixtures'/name))
        with self.assertRaisesRegex(AssetError,'requires its provenance'):
            verify_edition(ROOT/'tests/fixtures/supplemental-v1/sample-v1.0.0-offline.zip')

    def test_unknown_offline_format_fails_closed(self):
        manifest=json.loads((ROOT/'tests/fixtures/supplemental-v1/provenance.json').read_text())
        manifest['supplementary_package']['format']='press.offline.unknown'
        with self.assertRaisesRegex(AssetError,'unsupported offline package format'):package.package_names(manifest)


class OfflineBudgetTests(unittest.TestCase):
    def test_source_ci_counts_repeated_downloads_and_supporting_documents_at_boundary(self):
        root=Path(self.enterContext(tempfile.TemporaryDirectory()));manuscript=Path('works/sample/manuscript.md')
        (root/manuscript.parent/'supporting').mkdir(parents=True)
        (root/'asset-policy.json').write_text(json.dumps({'origin':ORIGIN}))
        (root/manuscript).write_text('![Inline]('+URL+')\n\n[First]('+DOWNLOAD+') and [repeat]('+DOWNLOAD+').\n\n[Claim](supporting/note.md#claim)\n')
        note=Path('works/sample/supporting/note.md')
        (root/note).write_text((ROOT/'templates/work/supporting/note.md').read_text()+'\n[Third]('+DOWNLOAD+').\n')
        bound=base64_size(len(SVG))+3*package.download_attribute_size(len(RAW),KEY)
        def fetch(policy,url):return (RAW,'application/octet-stream') if url==DOWNLOAD else (SVG,'image/svg+xml')
        with patch('verify_source_assets.git_sources',return_value=[manuscript,note]):
            with patch('verify_source_assets.MAX_TOTAL_BYTES',len(RAW)),self.assertRaisesRegex(AssetError,'unique asset byte limit'):
                verify_source_assets(root,fetcher=fetch)
            with patch('asset_policy.MAX_HTML_BYTES',bound):self.assertEqual(2,verify_source_assets(root,fetcher=fetch))
            with patch('asset_policy.MAX_HTML_BYTES',bound-1),self.assertRaisesRegex(AssetError,'offline HTML asset references'):
                verify_source_assets(root,fetcher=fetch)

    def test_download_budget_includes_full_longest_filename_attribute_and_base64_padding(self):
        for size in (0,1,2,3,257):
            raw=b'x'*size;digest=hashlib.sha256(raw).hexdigest();key='sha256/'+digest+'.jpeg';url=ORIGIN+'/'+key
            manifest={'asset_origin':ORIGIN,'assets':[{'role':'download','source_url':url}]}
            rendered=package.offline_html(('<body><a href="'+url+'">file</a></body>').encode(),manifest,{key:raw})
            attribute=re.search(rb'href="data:[^"]*" download="[^"]*"',rendered)[0]
            self.assertEqual(len(attribute),package.download_attribute_size(size,key))


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(loader.loadTestsFromName(name,cls) for cls in
        (OfflinePackageTests,OfflineHistoryTests,OfflineBudgetTests)
        for name in cls.__dict__ if name.startswith('test_'))
