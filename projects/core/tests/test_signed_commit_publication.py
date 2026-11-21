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
schema: guavajam.work.v1
title: Recorded Notes
author: Fixture Author
language: en
rights: all-rights-reserved
---

<a id="recording"></a>
## Recording

A reviewed recorded scene with [media](#field-audio) and a visible transcript.

<a id="field-audio"></a>
### Field audio

```guavajam-media
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
        (self.repo/'README.md').write_text('Authoring tools.\n')
        cmd(self.repo,'git','add','README.md')
        cmd(self.repo,'git','commit','-qm','Initialize authoring repository')
        self.draft=cmd(self.repo,'git','rev-parse','HEAD')
        home=Path(self.temporary.name)/'keys';home.mkdir(mode=0o700)
        self.env={**os.environ}
        self.key=home/'publisher'
        cmd(self.repo,'ssh-keygen','-q','-t','ed25519','-N','','-f',str(self.key))
        self.allowed_signers=home/'allowed-signers'
        self.allowed_signers.write_text('publisher '+self.key.with_suffix('.pub').read_text())
        self.trust={'allowed_signers': self.allowed_signers}
        cmd(self.repo,'git','config','user.signingkey',str(self.key))
        cmd(self.repo,'git','config','gpg.format','ssh')
        manuscript=self.repo/'content/manuscript.md'
        manuscript.parent.mkdir(parents=True)
        manuscript.write_text(MANUSCRIPT)
        (self.repo/'content/asset-policy.json').write_text('{"origin":"https://assets.example.org"}\n')
        self.signed_message='Record the field observation\n\nInclude the audio and reviewed transcript.'
        self._sign(self.signed_message)

    def _sign(self,message):
        cmd(self.repo,'git','add','-A')
        cmd(self.repo,'git','commit','-S','-m',message,env=self.env)
        self.signed=cmd(self.repo,'git','rev-parse','HEAD')

    def _verify(self,**kwargs):
        kwargs.setdefault('online_media',False)
        return verify(self.repo,self.signed,**self.trust,pandoc_version=False,**kwargs)

    def test_commit_and_source_path_are_canonical_not_url_or_tag(self):
        record=self._verify()
        self.assertEqual(self.signed,record['revision'])
        self.assertEqual('sha1',record['hash_algorithm'])
        self.assertEqual('content/manuscript.md',record['manuscript'])
        self.assertFalse(record['media_verified'])
        self.assertIn('field-audio',record['anchors'])
        self.assertEqual('audio',record['media'][0]['kind'])
        self.assertEqual('#transcript',record['media'][0]['transcript'])
        self.assertEqual({'schema':'guavajam.source-citation.v1','hash_algorithm':'sha1',
                          'revision':self.signed,'manuscript':record['manuscript'],
                          'anchor':'transcript'},citation(record,'transcript'))
        (self.repo/'content/manuscript.md').write_text('Uncommitted, unrelated contents.')
        self.assertEqual(self.signed,self._verify()['revision'])
        for invalid in ('HEAD', 'main', self.signed[:12], 'A'*40):
            with self.subTest(invalid=invalid),self.assertRaises(PublicationError):
                commit_identity(self.repo,invalid)

    def test_signature_must_be_independently_trusted(self):
        wrong=Path(self.temporary.name)/'wrong-signers'
        wrong.write_text('')
        with self.assertRaisesRegex(PublicationError,'untrusted'):
            verify(self.repo,self.signed,allowed_signers=wrong,pandoc_version=False)
        with self.assertRaisesRegex(PublicationError,'signed commit'):
            verify(self.repo,self.draft,**self.trust,pandoc_version=False)
        with self.assertRaises(PublicationError):
            signature(self.repo,self.signed)
        with self.assertRaises(PublicationError):
            citation(self._verify(),'../other')
        with self.assertRaisesRegex(PublicationError, 'existing stable source anchor'):
            citation(self._verify(), 'nonexistent-passage')

    def test_signature_authenticates_ordinary_message_and_exact_content_tree(self):
        record=self._verify()
        self.assertEqual(self.signed_message+'\n', record['message'])
        self.assertEqual(cmd(self.repo,'git','rev-parse',self.signed+':content'),record['tree'])
        cmd(self.repo,'git','commit','--allow-empty','-S','-m','Signed housekeeping',env=self.env)
        self.signed=cmd(self.repo,'git','rev-parse','HEAD')
        with self.assertRaisesRegex(PublicationError,'must change content'):
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
                        'Accept-Ranges':'bytes','ETag':'"fixture-etag"'}.get(name,default)
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
