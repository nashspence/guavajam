"""Recover signed source and exact deduplicated assets without the live repository or origin."""
import contextlib,hashlib,io,json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'scripts'),str(Path(__file__).parent)]
from source_archive import export,check
from asset_policy import AssetError
from test_signed_commit_publication import PublicationCommitTests
from media_fixture import audio

class ArchiveTests(PublicationCommitTests):
    def setUp(self):
        super().setUp()
        self.raw=audio(Path(self.temporary.name)/'fixture.mp3');self.digest=hashlib.sha256(self.raw).hexdigest()
        source=self.repo/'works/recorded-notes/manuscript.md'
        source.write_text(source.read_text().replace('a'*64,self.digest)+f'\n[Same recording](https://assets.example.org/sha256/{self.digest}.mp3)\n')
        from test_signed_commit_publication import cmd
        cmd(self.repo,'git','add','-A');cmd(self.repo,'git','commit','-qm','Review archive fixture');self._sign(self.signed_message)
        self.archive=Path(self.temporary.name)/'archive'
        self.enterContext(patch.dict(os.environ,{'GNUPGHOME':self.env['GNUPGHOME']}))
    @contextlib.contextmanager
    def received(self,url,mime,*,timeout):
        raw=self.raw
        class Response(io.BytesIO):
            status=200
            def getheader(self,name,default=None):return {'Content-Type':'audio/mpeg','Content-Length':str(len(raw))}.get(name,default)
        with Response(raw) as response:yield response
    def save(self):return export(self.repo,self.signed,self.archive,fingerprints=[self.fingerprint],response_factory=self.received)
    def test_archive_restores_without_origin_and_deduplicates_references(self):
        self.assertEqual(1,self.save());self.assertEqual(1,check(self.archive,self.signed,fingerprints=[self.fingerprint]))
        self.assertEqual(1,len(list((self.archive/'objects/sha256').iterdir())))
        self.assertTrue((self.archive/'source.bundle').is_file())
    def test_missing_asset_and_rehashed_inventory_cannot_change_signed_bytes(self):
        self.save();manifest=self.archive/'ARCHIVE.json';data=json.loads(manifest.read_text());key=next(iter(data['objects']))
        target=self.archive/'objects'/key;target.write_bytes(b'changed media')
        data['objects'][key]['sha256']=hashlib.sha256(target.read_bytes()).hexdigest();data['objects'][key]['byte_count']=target.stat().st_size;manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(AssetError,'signed source bytes'):check(self.archive,self.signed,fingerprints=[self.fingerprint])
    def test_inventory_cannot_omit_dependencies_or_select_a_different_revision(self):
        self.save();manifest=self.archive/'ARCHIVE.json';data=json.loads(manifest.read_text());data['objects']={};manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(AssetError,'inventory'):check(self.archive,self.signed,fingerprints=[self.fingerprint])
        with self.assertRaisesRegex(AssetError,'externally selected'):check(self.archive,'0'*40,fingerprints=[self.fingerprint])
    def test_existing_or_source_checkout_output_is_never_overwritten(self):
        with self.assertRaises(AssetError):export(self.repo,self.signed,self.repo/'saved',fingerprints=[self.fingerprint],response_factory=self.received)
        self.archive.mkdir()
        with self.assertRaises(AssetError):self.save()

def load_tests(loader,tests,pattern):return unittest.TestSuite(loader.loadTestsFromName(n,ArchiveTests) for n in ArchiveTests.__dict__ if n.startswith('test_'))
