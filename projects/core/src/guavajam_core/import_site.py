#!/usr/bin/env python3
"""Draft publisher settings from the last deployed catalog; never signs or overwrites.

After inspection, copy the result to press-site.json, commit it for review, and
sign an empty Press-Site: 1 checkpoint. Source commits and DOI bindings remain
unchanged. The old catalog remains an independently retained deployment baseline.
"""
import argparse
from pathlib import Path
import sys
from .catalog import read_catalog, encoded
from .publication_protocol import policy


def convert(catalog):
    settings = {k: v for k, v in catalog.items() if k not in {'schema', 'works'}}
    settings.update(schema='press.site.v1', works={}, editions={})
    for work in catalog['works']:
        settings['works'][work['slug']] = {k: v for k, v in work.items() if k not in {'slug', 'editions'}}
        # Preserve an intentionally absent recommendation during migration.
        settings['works'][work['slug']].setdefault('recommended_revision', None)
        for edition in work['editions']:
            settings['editions'][edition['revision']] = {k: v for k, v in edition.items() if k != 'revision'}
    policy(encoded(settings))
    return settings


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=Path('.build/press-site.json'))
    args=parser.parse_args(argv)
    try:
        settings=convert(read_catalog(args.catalog))
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open('xb') as stream:
            stream.write(encoded(settings))
    except (ValueError,OSError) as exc:
        print('Site migration draft failed: '+str(exc),file=sys.stderr);return 1
    print('Wrote unsigned settings draft:',args.output);return 0


if __name__=='__main__':
    sys.exit(main())
