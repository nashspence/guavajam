"""Create reviewable authoring material from the installed core resources."""
import argparse
import json
import os
import re
from pathlib import Path
import shutil
import yaml
from .repository import repository_root

from .resources import ROOT as RESOURCES
EXPORTS = {'capture': 'chatgpt-capture', 'credentials': 'storage/credentials.env.example',
           'preview': 'storage/preview.svg'}


def main(command):
    parser = argparse.ArgumentParser(description=__doc__)
    if command == 'resources':
        parser.add_argument('name', choices=EXPORTS)
        parser.add_argument('--output', type=Path, required=True)
    else:
        parser.add_argument('--title', required=True)
        parser.add_argument('--author', required=True)
        parser.add_argument('--language', default='en')
    args = parser.parse_args()
    try:
        if command == 'resources':
            source = RESOURCES / EXPORTS[args.name]
            if args.output.exists() or args.output.is_symlink():
                raise ValueError('output must be new')
            if source.is_dir():
                shutil.copytree(source, args.output)
            else:
                with open(args.output, 'xb', opener=lambda path, flags: os.open(path, flags, 0o600)) as stream:
                    stream.write(source.read_bytes())
            return 0
        root = repository_root()
        target = root / 'content'
        if target.is_symlink():
            raise ValueError('content must not be a symlink')
        source = (RESOURCES / 'templates/work/manuscript.md').read_text()
        _, front, body = source.split('---', 2)
        facts = yaml.safe_load(front)
        facts.update(title=args.title, author=args.author, language=args.language)
        body = body.replace('# Sample Work', '# ' + args.title, 1)
        # A fresh starter carries its original sample asset locally.
        origin = json.loads((RESOURCES / 'asset-policy.json').read_text())['origin']
        body = re.sub(re.escape(origin) + r'/sha256/[a-f0-9]{64}\.svg', 'assets/preview.svg', body)
        from .validate_sources import validate_work_fields, parse_pandoc
        fields = validate_work_fields(Path('content/manuscript.md'), facts,
                                      parse_pandoc('# ' + args.title)['blocks'][0]['c'][2])
        if fields:
            raise ValueError('; '.join(fields))
        target.parent.mkdir(exist_ok=True)
        target.mkdir()  # Refuse existing source, including a symlink.
        shutil.copytree(RESOURCES / 'templates/work/supporting', target / 'supporting')
        (target / 'assets').mkdir()
        shutil.copyfile(RESOURCES / 'storage/preview.svg', target / 'assets/preview.svg')
        (target / 'manuscript.md').write_text('---\n' + yaml.safe_dump(facts, sort_keys=False, allow_unicode=True) + '---' + body)
        makefile = root / 'Makefile'
        if not makefile.exists() and not makefile.is_symlink():
            with makefile.open('x') as stream:
                stream.write('PYTHON ?= python\ninclude $(shell $(PYTHON) -m guavajam_core.resources)\n')
        print('Created draft:', target.relative_to(root))
        return 0
    except (ValueError, OSError) as exc:
        parser.exit(1, str(exc) + '\n')
