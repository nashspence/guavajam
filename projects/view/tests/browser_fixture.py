"""Build real copied-starter, supporting and native-media browser qualification views."""
from pathlib import Path
import base64,hashlib,json,subprocess,os
from guavajam.renderer import render
from guavajam_test_support.media_fixture import video,audio
from PIL import Image
from guavajam_test_support import FIXTURES, RESOURCES
ROOT=Path(__file__).resolve().parents[1]
ORIGIN='https://media.example.test'


def make_fixture(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    repo=directory/'source';repo.mkdir();work=repo/'works/sample-work';(work/'supporting').mkdir(parents=True);(work/'assets').mkdir()
    def git(*args):return subprocess.check_output(['git',*args],cwd=repo,stderr=subprocess.PIPE).decode().strip()
    for args in [('init','-q','-b','main'),('config','user.name','Fixture'),('config','user.email','fixture@example.org')]:git(*args)
    (repo/'asset-policy.json').write_text(json.dumps({'origin':ORIGIN}))
    source=(RESOURCES / 'templates/work/manuscript.md').read_text()
    import re
    source=re.sub(r'https://media\.example\.org/sha256/[^\s]+\.svg','assets/preview.svg',source)
    (work/'assets/preview.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"><rect width="100" height="50" fill="navy"/></svg>\n')
    (work/'supporting/note.md').write_text((RESOURCES / 'templates/work/supporting/note.md').read_text())
    for branch in ('current','alternate'):
        (work/'supporting'/('lantern-'+branch+'.md')).write_text((FIXTURES/'chatgpt'/('lantern-'+branch+'.gfm.md')).read_text())
    with (work/'supporting/note.md').open('a') as stream:
        stream.write('\n<a name="time"></a>\n\n*<time datetime="2026-11-08T14:00:00Z">8 Nov 2026 · 14:00 UTC</time>*\n\n<a name="quote" data-guavajam-speaker="assistant"></a>\n\n> <a name="quote-heading"></a>\n>\n> ## Quoted heading\n>\n> <a name="quote-body"></a>\n>\n> A quoted paragraph with an independently citable element.\n\n— gpt-6-thinking (max effort)\n')
    assets={}
    def asset(name,raw,mime):
        digest=hashlib.sha256(raw).hexdigest();key='/sha256/'+digest+'.'+name.rsplit('.',1)[1]
        assets[key]={'body':base64.b64encode(raw).decode(),'mime':mime};return ORIGIN+key
    media=directory/'media';media.mkdir()
    movie=asset('clip.mp4',video(media/'clip.mp4'),'video/mp4');sound=asset('sound.mp3',audio(media/'sound.mp3'),'audio/mpeg')
    Image.new('RGB',(64,36),(20,80,120)).save(media/'poster.png');poster=asset('poster.png',(media/'poster.png').read_bytes(),'image/png')
    track=asset('captions.vtt',b'WEBVTT\n\n00:00:00.000 --> 00:00:01.500\nA short synthetic qualification recording.\n','text/vtt')
    for ident,spec in [('movie',{'kind':'video','src':movie,'poster':poster,'captions':track,'language':'en','label':'English'}),('sound',{'kind':'audio','src':sound})]:
        declaration={'id':ident,'title':'Qualification '+ident,'transcript':'#transcript',**spec}
        source+='\n<a id="'+ident+'"></a>\n## '+ident.title()+'\n\n```guavajam-media\n'+json.dumps(declaration,indent=2)+'\n```\n'
    source+='\n<a id="transcript"></a>\n## Recording transcript\n\nA short synthetic recording contains silence and changing color. The caption describes the qualification scene.\n'
    source+='\n<a id="wide"></a>\n## Wide structures\n\n```python\nlong_identifier = "'+'x'*240+'"\n```\n\n'
    source+='| '+' | '.join('Column '+str(i) for i in range(12))+' |\n| '+' | '.join('---' for _ in range(12))+' |\n| '+' | '.join('A long measured value' for _ in range(12))+' |\n\n: Table 2 — Wide measurements\n'
    (work/'manuscript.md').write_text(source)
    git('add','-A');git('commit','-qm','Qualification source')
    (repo/'guavajam.json').write_text(json.dumps({'schema':'guavajam.repository.v1','publisher':'Fixture Publisher',
                                                      'base_url':'https://publisher.example'})+'\n')
    git('add','guavajam.json');git('commit','-qm','Review publisher repository settings')
    keys=directory/'signing';keys.mkdir(mode=0o700)
    env={**os.environ,'GNUPGHOME':str(keys)}
    def gpg(*args):return subprocess.check_output(['gpg',*args],env=env,stderr=subprocess.PIPE).decode().strip()
    gpg('--batch','--pinentry-mode','loopback','--passphrase','','--quick-generate-key',
        'Browser Preview <fixture@example.org>','ed25519','sign','0')
    fingerprint=next(line.split(':')[9] for line in gpg('--batch','--with-colons','--list-secret-keys').splitlines() if line.startswith('fpr:'))
    git('config','gpg.format','openpgp');git('config','user.signingkey',fingerprint)
    subprocess.run(['git','commit','--allow-empty','-S','-qm','Authorize repository settings\n\nGuavaJam-Repository: 1'],cwd=repo,env=env,check=True,capture_output=True)
    with (work/'manuscript.md').open('a') as stream:stream.write('\nA proposed additional work passage.\n')
    git('add','-A');git('commit','-qm','Proposed unsigned complete site');sha=git('rev-parse','HEAD')
    output=directory/'view';render(repo,sha,'sample-work',output,preview=True)
    from guavajam.preview import build_preview
    from guavajam_core.asset_versions import pin_key
    from guavajam_core.gateway_index import Index
    from guavajam_test_support.storage_fixture import MemoryStore
    store=MemoryStore()
    for key,record in assets.items():
        store.upload(key.lstrip('/'),base64.b64decode(record['body']),record['mime'])
        pin_key(store,Index(store),key.lstrip('/'))
    private=directory/'private'
    from unittest.mock import patch
    with patch.dict(os.environ,{'GNUPGHOME':str(keys)}):
        manifest=build_preview(repo,sha,'sample-work',private,store=store,
                               fingerprints=[fingerprint],published='2026-11-15')
    # The same full media journey must also work before any signer bootstrap.
    from guavajam_test_support.discovery_fixture import Draft
    import shutil
    bootstrap = Draft(directory/'bootstrap')
    bootstrap.git('rm', '-r', 'works/field-notes')
    shutil.copytree(work, bootstrap.repo/'works/sample-work')
    shutil.copyfile(repo/'asset-policy.json', bootstrap.repo/'asset-policy.json')
    bootstrap.git('add', '-A'); bootstrap.git('commit', '-qm', 'Review first private work')
    bootstrap_sha = bootstrap.git('rev-parse', 'HEAD')
    bootstrap_output = directory/'bootstrap-private'
    bootstrap_manifest = build_preview(bootstrap.repo, bootstrap_sha, 'sample-work', bootstrap_output,
                                       store=store, published='2026-11-15')
    fixture={'root':str(output),'origin':ORIGIN,'assets':assets,'revision':sha,
             'preview_root':str(private),'preview_manifest':manifest,
             'bootstrap':{'revision':bootstrap_sha,'preview_root':str(bootstrap_output),
                          'preview_manifest':bootstrap_manifest}}
    (directory/'fixture.json').write_text(json.dumps(fixture))
    return fixture
