"""Exercise an independent Git repository, real Pandoc, and real OpenPGP signing."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from guavajam.media import decode, declarations, MediaError
from a_guavajam_static_website_generator.renderer import RenderError, render, revision

SRC = '''---
schema: guavajam.work.v1
title: Field Notes
author: Example Author
language: en
rights: all-rights-reserved
---

<a id="opening"></a>
## Opening

See [the recording](#demonstration), [the source](supporting/note.md#claim), and [the transcript](#demo-transcript).

<a id="demonstration"></a>
## Recording

```guavajam-media
{
  "id": "demonstration",
  "kind": "video",
  "title": "Field recording",
  "src": "https://media.example.org/sha256/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.mp4",
  "poster": "https://media.example.org/sha256/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.webp",
  "captions": "https://media.example.org/sha256/cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc.vtt",
  "language": "en",
  "label": "English",
  "transcript": "#demo-transcript"
}
```

<a id="demo-transcript"></a>
## Transcript

The speaker says hello, and the background wind increases.
'''
SUPPORT = '''<a name="document"></a>

# Supporting note

<a name="claim"></a>

Independent claim with a stable source anchor.
'''
ORIGIN = 'https://media.example.org'


def command(root, *args, env=None):
    p = subprocess.run(args, cwd=root, env=env, capture_output=True, text=True)
    if p.returncode: raise AssertionError(f'{args}: {p.stderr}')
    return p.stdout.strip()


class PortableViewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'source'
        self.root.mkdir()
        command(self.root, 'git','init','-q','-b','main')
        command(self.root, 'git','config','user.name','Fixture')
        command(self.root, 'git','config','user.email','fixture@example.org')
        (self.root/'content').mkdir(exist_ok=True)
        (self.root/'content/asset-policy.json').write_text('{"origin": "'+ORIGIN+'"}\n')
        location = self.root/'content'
        (location/'supporting').mkdir(parents=True)
        (location/'manuscript.md').write_text(SRC)
        (location/'supporting/note.md').write_text(SUPPORT)
        command(self.root, 'git','add','-A')
        command(self.root, 'git','commit','-qm','Draft')
        self.rev = command(self.root, 'git','rev-parse','HEAD')
        self.output = Path(self.temporary.name) / 'view'

    def test_pinned_git_revision_renders_portably_with_no_runtime_scripts(self):
        self.assertEqual(2,render(self.root,self.rev,self.output,preview=True))
        text = (self.output/'index.html').read_text()
        self.assertIn('Unsigned proposed edition, not issued.',text)
        self.assertNotIn('UNSIGNED PREVIEW',text)
        self.assertIn('Git SHA-1 '+self.rev,text)
        self.assertEqual(1,text.count('id="opening"'))
        self.assertIn('href="supporting/note.html#claim"',text)
        self.assertIn('<video controls playsinline',text)
        self.assertIn('<track kind="captions"',text)
        self.assertIn('Content-Security-Policy',text)
        self.assertIn('media-src https://media.example.org',text)
        self.assertNotIn('<script',text)
        self.assertIn('id="claim"',(self.output/'supporting/note.html').read_text())
        view=json.loads((self.output/'VIEW.json').read_text())
        self.assertEqual(self.rev,view['canonical']['revision'])
        self.assertEqual('content/manuscript.md',view['canonical']['manuscript'])
        self.assertTrue(view['preview'])
        with self.assertRaisesRegex(RenderError,'new or empty'):
            render(self.root,self.rev,self.output,preview=True)

    def test_draft_identity_is_rendered_only_as_one_metadata_citation(self):
        render(self.root, self.rev, self.output, preview=True)
        html = (self.output / 'index.html').read_text()
        body = html.split('<body>', 1)[1]
        citations = re.findall(r'<div class="guavajam-citation".*?</a></div>', body, re.S)
        self.assertEqual(1, len(citations))
        self.assertIn('Example Author. <h1 class="guavajam-citation-title"><cite>Field Notes</cite></h1>.', citations[0])
        self.assertIn('role="group" aria-label="Git source citation"', citations[0])
        self.assertIn('Download draft source identity JSON', citations[0])
        self.assertNotIn('Field Notes', body.replace(citations[0], ''))
        self.assertNotIn('Example Author', body.replace(citations[0], ''))
        self.assertEqual(1, body.count('<h1'))
        self.assertLess(body.index('<article'), body.index(citations[0]))
        self.assertLess(body.index(citations[0]), body.index('</article>'))
        self.assertIn('Unsigned proposed edition, not issued.', body)
        citation = json.loads((self.output / 'source.citation.json').read_text())
        self.assertEqual(self.rev, citation['revision'])
        self.assertEqual('guavajam.source-citation.v1', citation['schema'])

    def test_revision_is_never_a_branch_or_floating_tag(self):
        for wrong in ('HEAD','main','v1','A'*40,'a'*39,'a'*41):
            with self.subTest(wrong=wrong),self.assertRaises(RenderError):
                revision(self.root,wrong)
        # A newer commit must not change an earlier view's citation or content.
        (self.root/'content/manuscript.md').write_text(SRC.replace('Field Notes','Changed Notes'))
        command(self.root,'git','add','-A');command(self.root,'git','commit','-qm','Change after publication')
        render(self.root,self.rev,self.output,preview=True)
        self.assertIn('<title>Field Notes', (self.output/'index.html').read_text())
        self.assertNotIn('Changed Notes', (self.output/'index.html').read_text())

    def test_all_unreviewed_html_and_external_media_are_rejected(self):
        for change in ('<script>alert(1)</script>',
                       '<img src="https://evil.example/spy">',
                       '[bad](javascript:alert(1))',
                       '![bad](https://evil.example/x.png)'):
            with self.subTest(change=change):
                manuscript=self.root/'content/manuscript.md'
                manuscript.write_text(SRC+'\n'+change+'\n')
                command(self.root,'git','add','-A');command(self.root,'git','commit','-qm','Unsafe example')
                newsha=command(self.root,'git','rev-parse','HEAD')
                with self.assertRaises((RenderError,MediaError)):
                    render(self.root,newsha,self.output,preview=True)
        self.assertFalse(self.output.exists())

    def test_valid_signature_is_required_without_explicit_preview(self):
        with self.assertRaisesRegex(RenderError,'external GPG fingerprint or SSH'):
            render(self.root,self.rev,self.output)

    def test_ssh_trust_file_must_be_external_and_cannot_be_a_preview_fallback(self):
        key=Path(self.temporary.name)/'inside-policy-key'
        command(self.root,'ssh-keygen','-q','-t','ed25519','-N','','-f',str(key))
        command(self.root,'git','config','gpg.format','ssh')
        command(self.root,'git','config','user.signingkey',str(key))
        command(self.root,'git','commit','--amend','-S','-m','Publish Field Notes')
        candidate=command(self.root,'git','rev-parse','HEAD')
        signers=self.root/'allowed-signers'
        signers.write_text('fixture namespaces="git" '+key.with_suffix('.pub').read_text())
        with self.assertRaisesRegex(RenderError,'external bounded authority'):
            render(self.root,candidate,self.output,allowed_signers=signers)
        with self.assertRaisesRegex(RenderError,'preview cannot be combined'):
            render(self.root,candidate,self.output,allowed_signers=signers,preview=True)
        signers.unlink()

    def test_signed_revision_with_external_trust_is_rendered(self):
        if not shutil.which('gpg') or not shutil.which('gpgconf'):
            self.skipTest('OpenPGP agent unavailable; SSH signatures exercised separately')
        home=Path(self.temporary.name)/'gpg';home.mkdir(mode=0o700)
        env={**os.environ,'GNUPGHOME':str(home)}
        agent=subprocess.run(['gpgconf','--launch','gpg-agent'],env=env,capture_output=True,timeout=30,check=False)
        if agent.returncode:
            self.skipTest('OpenPGP agent unavailable; SSH signatures exercised separately')
        command(self.root,'gpg','--batch','--pinentry-mode','loopback','--passphrase','',
                '--quick-generate-key','Viewer Fixture <fixture@example.org>','ed25519','sign','0',env=env)
        listing=command(self.root,'gpg','--batch','--with-colons','--list-secret-keys',env=env)
        fingerprint=next(x.split(':')[9] for x in listing.splitlines() if x.startswith('fpr:'))
        command(self.root,'git','config','user.signingkey',fingerprint)
        command(self.root,'git','config','gpg.format','openpgp')
        command(self.root,'git','-c','commit.gpgSign=true','commit','--amend','-S',
                '-m','Publish Field Notes',env=env)
        signed=command(self.root,'git','rev-parse','HEAD')
        with patch.dict(os.environ,{'GNUPGHOME':str(home)}):
            self.assertEqual(2,render(self.root,signed,self.output,fingerprints=[fingerprint]))
            self.assertIn('Verified signed source',(self.output/'index.html').read_text())
            with self.assertRaisesRegex(RenderError,'untrusted'):
                render(self.root,signed,Path(self.temporary.name)/'bad', fingerprints=['0'*40])

    def test_real_ssh_publication_uses_only_external_signer_authority(self):
        key=Path(self.temporary.name)/'fixture-key'
        command(self.root,'ssh-keygen','-q','-t','ed25519','-N','','-f',str(key))
        command(self.root,'git','config','gpg.format','ssh');command(self.root,'git','config','user.signingkey',str(key))
        command(self.root,'git','commit','--amend','-S','-m','Publish fixture')
        revision=command(self.root,'git','rev-parse','HEAD')
        signers=Path(self.temporary.name)/'allowed-signers';signers.write_text('fixture namespaces="git" '+key.with_suffix('.pub').read_text())
        self.assertEqual(2,render(self.root,revision,self.output,allowed_signers=signers))
        self.assertIn('Verified signed source',(self.output/'index.html').read_text())


class ContractTests(unittest.TestCase):
    def test_duplicate_unknown_and_untrusted_media_fields_fail(self):
        valid=json.loads(SRC.split('```guavajam-media\n',1)[1].split('\n```',1)[0])
        self.assertEqual('video',decode(json.dumps(valid),ORIGIN)['kind'])
        variants=[{**valid,'src':'https://evil.example/sha256/'+'a'*64+'.mp4'},
                  {**valid,'src':'http://media.example.org/sha256/'+'a'*64+'.mp4'},
                  {**valid,'src':valid['src']+'?versionId=x'},
                  {**valid,'autoplay':'true'},
                  {**valid,'captions':None},
                  {**valid,'sound':'none'},
                  {k:v for k,v in valid.items() if k!='poster'}]
        for item in variants:
            with self.subTest(item=str(item)[:150]),self.assertRaises(MediaError):
                decode(json.dumps(item),ORIGIN)
        with self.assertRaises(MediaError):
            decode('{"kind":"audio","kind":"video"}', ORIGIN)
