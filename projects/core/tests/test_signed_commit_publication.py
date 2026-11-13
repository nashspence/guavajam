"""Canonical commit citation, trusted signatures and large streamed media."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
from guavajam_core.publication_commit import verify, commit_identity, signature, citation, PublicationError
from guavajam_core.media import decode, MediaError
from guavajam_core.verify_media_sources import verify_one, verify_items
from guavajam_core.asset_policy import AssetError

MANUSCRIPT='''---
schema: press.work.v1
title: Recorded Notes
author: Fixture Author
slug: recorded-notes
language: en
status: draft
rights: all-rights-reserved
---

# Recorded Notes

<a id="recording"></a>
## Recording

A reviewed recorded scene with [media](#field-audio) and a visible transcript.

<a id="field-audio"></a>
### Field audio

```press-media
{
  "id": "field-audio",
  "kind": "audio",
  "src": "https://assets.example.org/sha256/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.mp3",
  "title": "A quiet field recording",
  "transcript": "#transcript"
}
```

<a id="transcript"></a>
## Transcript

A quiet field recording containing wind, birds and spoken words.
'''


def cmd(root,*args,env=None):
    p=subprocess.run(args,cwd=root,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False,text=True)
    if p.returncode:
        raise AssertionError(f'{args}: {p.stderr[:600]}')
    return p.stdout.strip()


class PublicationCommitTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.repo=Path(self.temporary.name)/'repo';self.repo.mkdir()
        cmd(self.repo,'git','init','-q','-b','main')
        cmd(self.repo,'git','config','user.name','Fixture Author')
        cmd(self.repo,'git','config','user.email','fixture@example.org')
        (self.repo/'asset-policy.json').write_text('{"origin":"https://assets.example.org"}\n')
        manuscript=self.repo/'works/recorded-notes/manuscript.md'
        manuscript.parent.mkdir(parents=True)
        manuscript.write_text(MANUSCRIPT)
        cmd(self.repo,'git','add','-A')
        cmd(self.repo,'git','commit','-qm','Draft')
        self.draft=cmd(self.repo,'git','rev-parse','HEAD')
        if not shutil.which('gpg'):self.skipTest('GPG required for signed publication tests')
        home=Path(self.temporary.name)/'gpg';home.mkdir(mode=0o700)
        self.env={**os.environ,'GNUPGHOME':str(home)}
        cmd(self.repo,'gpg','--batch','--pinentry-mode','loopback','--passphrase','',
            '--quick-generate-key','Test Publisher <fixture@example.org>','ed25519','sign','0',env=self.env)
        listing=cmd(self.repo,'gpg','--batch','--with-colons','--list-secret-keys',env=self.env)
        self.fingerprint=next(row.split(':')[9] for row in listing.splitlines() if row.startswith('fpr:'))
        cmd(self.repo,'git','config','user.signingkey',self.fingerprint)
        cmd(self.repo,'git','config','gpg.format','openpgp')
        self.signed_message='Press-Publication: 1\nPress-Work: works/recorded-notes/manuscript.md'
        self._sign(self.signed_message)

    def _sign(self,message):
        cmd(self.repo,'git','-c','commit.gpgSign=true','commit','--allow-empty','-S',
            '-m','Publish Recorded Notes','-m',message,env=self.env)
        self.signed=cmd(self.repo,'git','rev-parse','HEAD')

    def _verify(self,**kwargs):
        kwargs.setdefault("online_media",False)
        with patch.dict(os.environ,{'GNUPGHOME':self.env['GNUPGHOME']}):
            return verify(self.repo,self.signed,fingerprints=[self.fingerprint],
                          pandoc_version=False,**kwargs)

    def test_commit_and_source_path_are_canonical_not_url_or_tag(self):
        record=self._verify()
        self.assertEqual(self.signed,record['revision'])
        self.assertEqual('sha1',record['hash_algorithm'])
        self.assertEqual('works/recorded-notes/manuscript.md',record['manuscript'])
        self.assertFalse(record['media_verified'])
        self.assertIn('field-audio',record['anchors'])
        self.assertEqual('audio',record['media'][0]['kind'])
        self.assertEqual('#transcript',record['media'][0]['transcript'])
        self.assertEqual({'schema':'press.citation.v1','hash_algorithm':'sha1',
                          'revision':self.signed,'manuscript':record['manuscript'],
                          'anchor':'transcript'},citation(record,'transcript'))
        (self.repo/'works/recorded-notes/manuscript.md').write_text('Uncommitted, unrelated contents.')
        self.assertEqual(self.signed,self._verify()['revision'])
        for invalid in ('HEAD', 'main', self.signed[:12], 'A'*40):
            with self.subTest(invalid=invalid),self.assertRaises(PublicationError):
                commit_identity(self.repo,invalid)

    def test_signature_must_be_independently_trusted(self):
        with patch.dict(os.environ,{'GNUPGHOME':self.env['GNUPGHOME']}):
            with self.assertRaisesRegex(PublicationError,'not in the independent trust policy'):
                verify(self.repo,self.signed,fingerprints=['0'*40],pandoc_version=False)
            with self.assertRaisesRegex(PublicationError,'must be in a signed commit'):
                verify(self.repo,self.draft,fingerprints=[self.fingerprint],pandoc_version=False)
        with self.assertRaises(PublicationError):
            signature(self.repo,self.signed)
        with self.assertRaises(PublicationError):
            citation(self._verify(),'../other')
        with self.assertRaisesRegex(PublicationError, 'existing stable source anchor'):
            citation(self._verify(), 'nonexistent-passage')

    def test_publication_trailers_are_exact_and_unique(self):
        self._sign('Press-Publication: 1\nPress-Work: works/another/manuscript.md')
        with self.assertRaises(PublicationError):
            self._verify()
        self._sign('Press-Publication: 1\nPress-Work: works/recorded-notes/manuscript.md\nOther: trailer')
        with self.assertRaisesRegex(PublicationError,'final block'):
            self._verify()
        self._sign(self.signed_message+'\nPress-Publication: 1')
        with self.assertRaisesRegex(PublicationError,'trailers must remain unchanged'):
            self._verify()

    def test_online_verification_is_the_publication_default(self):
        with patch('guavajam_core.verify_source_assets.verify_source_assets',return_value=1) as run:
            self._verify(online_media=True)
            run.assert_called_once()
        with patch('guavajam_core.verify_source_assets.verify_source_assets',side_effect=AssetError('mismatch')):
            with self.assertRaisesRegex(AssetError,'mismatch'):
                self._verify(online_media=True)


class StreamAndMediaTests(unittest.TestCase):
    def test_media_input_is_a_closed_portable_data_contract(self):
        good={'kind':'audio','id':'ambient','title':'Ambient sound','transcript':'#recording',
              'src':'https://assets.example.org/sha256/'+'a'*64+'.mp3'}
        self.assertEqual(good,decode(json.dumps(good),'https://assets.example.org'))
        for bad in [{**good,'autoplay':'true'}, {**good,'src':'https://evil.example/sha256/'+'a'*64+'.mp3'},
                    {**good,'src':good['src']+'?x=1'}, {**good,'transcript':'https://bad.example'}]:
            with self.subTest(bad=bad),self.assertRaises(MediaError):
                decode(json.dumps(bad),'https://assets.example.org')

    def test_large_media_streams_and_hashes_without_a_ten_mib_cap(self):
        data=(b'\x01\x02\x03\x04'*1024)*(3073)  # 12.0 MiB+; no full-object memory join in verifier.
        digest=hashlib.sha256(data).hexdigest()
        url='https://assets.example.org/sha256/'+digest+'.mp4'
        class Response:
            status=200
            def __init__(self):self.reader=io.BytesIO(data);self.maximum=0
            def getheader(self,name,default=None):
                return {'Content-Type':'video/mp4','Content-Length':str(len(data)),
                        'Accept-Ranges':'bytes'}.get(name,default)
            def read1(self,count):
                self.maximum=max(self.maximum,count)
                return self.reader.read(count)
        from contextlib import contextmanager
        responses=[]
        @contextmanager
        def factory(_url,_accept,*,timeout):
            self.assertEqual('video/mp4',_accept)
            self.assertGreater(timeout,30)
            resp=Response();responses.append(resp)
            yield resp
        with patch('guavajam_core.media_formats.inspect',return_value={}):
            self.assertEqual(len(data),verify_one(url,'https://assets.example.org',response_factory=factory))
        self.assertLessEqual(responses[0].maximum,65536)
        self.assertEqual({url:len(data)},verify_items([{'src':url},{'src':url}],
            'https://assets.example.org',verifier=lambda *_:len(data)))
        with self.assertRaisesRegex(AssetError,'byte budget'):
            with patch('guavajam_core.verify_media_sources.MAX_MEDIA_TOTAL',len(data)-1):
                verify_items([{'src':url}], 'https://assets.example.org',verifier=lambda *_:len(data))
        with self.assertRaises(AssetError):
            wrong=url.replace(digest,'f'*64)
            verify_one(wrong,'https://assets.example.org',response_factory=factory)



def load_tests(loader, tests, pattern):
    return unittest.TestSuite(loader.loadTestsFromName(name,cls) for cls in (PublicationCommitTests,StreamAndMediaTests,) for name in cls.__dict__ if name.startswith("test_"))
