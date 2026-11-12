"""Build real copied-starter, supporting and native-media browser qualification views."""
from pathlib import Path
import base64,hashlib,json,subprocess
from guavajam.renderer import render
from media_fixture import video,audio
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
ORIGIN='https://media.example.test'


def make_fixture(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    repo=directory/'source';repo.mkdir();work=repo/'works/sample-work';(work/'supporting').mkdir(parents=True);(work/'assets').mkdir()
    def git(*args):return subprocess.check_output(['git',*args],cwd=repo,stderr=subprocess.PIPE).decode().strip()
    for args in [('init','-q','-b','main'),('config','user.name','Fixture'),('config','user.email','fixture@example.org')]:git(*args)
    (repo/'asset-policy.json').write_text(json.dumps({'origin':ORIGIN}))
    source=(ROOT/'tests/fixtures/manuscript.md').read_text()
    import re
    source=re.sub(r'https://assets[^\s]+\.svg','assets/preview.svg',source)
    (work/'assets/preview.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"><rect width="100" height="50" fill="navy"/></svg>\n')
    (work/'supporting/note.md').write_text((ROOT/'tests/fixtures/note.md').read_text())
    for branch in ('current','alternate'):
        (work/'supporting'/('lantern-'+branch+'.md')).write_text((ROOT/'tests/fixtures'/('lantern-'+branch+'.gfm.md')).read_text())
    with (work/'supporting/note.md').open('a') as stream:
        stream.write('\n<a name="time"></a>\n\n*<time datetime="2026-11-08T14:00:00Z">8 Nov 2026 · 14:00 UTC</time>*\n\n<a name="quote" data-press-speaker="assistant"></a>\n\n> <a name="quote-heading"></a>\n>\n> ## Quoted heading\n>\n> <a name="quote-body"></a>\n>\n> A quoted paragraph with an independently citable element.\n\n— gpt-6-thinking (max effort)\n')
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
        source+='\n<a id="'+ident+'"></a>\n## '+ident.title()+'\n\n```press-media\n'+json.dumps(declaration,indent=2)+'\n```\n'
    source+='\n<a id="transcript"></a>\n## Recording transcript\n\nA short synthetic recording contains silence and changing color. The caption describes the qualification scene.\n'
    source+='\n<a id="wide"></a>\n## Wide structures\n\n```python\nlong_identifier = "'+'x'*240+'"\n```\n\n'
    source+='| '+' | '.join('Column '+str(i) for i in range(12))+' |\n| '+' | '.join('---' for _ in range(12))+' |\n| '+' | '.join('A long measured value' for _ in range(12))+' |\n\n: Table 2 — Wide measurements\n'
    (work/'manuscript.md').write_text(source)
    git('add','-A');git('commit','-qm','Qualification source');sha=git('rev-parse','HEAD')
    output=directory/'view';render(repo,sha,'sample-work',output,preview=True)
    fixture={'root':str(output),'origin':ORIGIN,'assets':assets,'revision':sha}
    (directory/'fixture.json').write_text(json.dumps(fixture))
    return fixture
