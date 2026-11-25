#!/usr/bin/env python3
"""Reproduce primary storage, correctness and browser experiments in a fresh output directory.

Install the pinned repository/tool dependencies first. This runner deliberately
does not install software, download browsers, or claim a release qualification.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
BASE = '54fce07b619628a5e92be946be4b189873ddf1a0'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--guavajam', type=Path, required=True, help='Separate checkout at the tested base commit')
    parser.add_argument('--output', type=Path, required=True, help='Scratch output outside this reference payload')
    parser.add_argument('--phase', choices=['storage','verify','browser','chunks'], required=True)
    parser.add_argument('--editions', type=int, choices=[1,5], default=1)
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--node', default='node')
    parser.add_argument('--chrome', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    guavajam, out = args.guavajam.resolve(), args.output.resolve()
    if out == ROOT or ROOT in out.parents:
        parser.error('Output must be outside the immutable reference payload')
    revision = subprocess.check_output(['git','-C',str(guavajam),'rev-parse','HEAD'],text=True).strip()
    if revision != BASE:
        parser.error(f'Expected tested GuavaJam base {BASE}; got {revision}')
    if subprocess.run(['git','-C',str(guavajam),'diff','--quiet','HEAD','--'],check=False).returncode:
        parser.error('The tested GuavaJam checkout has tracked modifications; use a separate clean base checkout')
    if args.phase in ['browser','chunks'] and not args.chrome:
        parser.error('--chrome is required for browser phases; install the browser explicitly')
    scripts = ROOT / 'experiments'
    corpus = out / 'guavajam-documents.jsonl'
    data = out / f'editions-{args.editions}'
    cases = data / 'transport-cases.json'

    def run(command):
        command = [str(value) for value in command]
        print(shlex.join(command), flush=True)
        if not args.dry_run:
            subprocess.run(command, check=True, cwd=out)

    if not args.dry_run:
        out.mkdir(parents=True,exist_ok=True)
    if args.phase == 'storage':
        if not args.dry_run:
            if data.exists():
                parser.error('Use a fresh output directory for storage; existing measurements are not overwritten')
            with gzip.open(ROOT / 'experiments/corpus/guavajam-documents.jsonl.gz','rb') as source, corpus.open('wb') as target:
                shutil.copyfileobj(source,target)
            with corpus.open('rb') as source:
                assert hashlib.file_digest(source,'sha256').hexdigest() == 'ecb80dfe215b458c119ca6e0265723cdab5e7210410f864ace5b2c6a059f610a'
        run([args.python,scripts/'storage_matrix.py','--guavajam',guavajam,'--corpus',corpus,
             '--output',data,'--editions',args.editions,'--change-rate','0.05','--metadata-mode','edition',
             '--variants','baseline,compact,segmented'])
        run([args.python,scripts/'directory_partition.py',data/'baseline.sqlite',data/'directory.sqlite'])
        run([args.python,scripts/'export_range_cases.py','--database',data/'baseline.sqlite','--output',cases])
    elif args.phase == 'verify':
        run([args.python,scripts/'verify_navigation_indexes.py',data,corpus,
             ROOT/'results/storage/nav-v2latest/browser-cases.json','--repetitions','1'])
        if not args.dry_run:
            fidelity = json.loads((data/'source-fidelity.json').read_text())
            assert fidelity['source_sha256_matches_actual_markdown'], fidelity
    elif args.phase == 'browser':
        # The convenience transport run deliberately uses three fixed cases.
        # The unchanged production worker matrix below is a separate check.
        run([args.node,scripts/'browser_matrix.mjs',guavajam,args.chrome.resolve(),data,cases,
             out/f'browser-{args.editions}','baseline,segmented','controlled','1'])
        if args.editions == 1:
            run([args.node,scripts/'production_worker_parity.mjs',guavajam,args.chrome.resolve(),data,
                 out/f'production-worker-{args.editions}.json','baseline,compact,segmented'])
            run([args.node,scripts/'browser_matrix.mjs',guavajam,args.chrome.resolve(),data,
                 ROOT/'results/storage/nav-v2latest/directory-cases.json',out/'browser-directory',
                 'directory','controlled','1'])
    else:
        run([args.node,scripts/'range-http/run-chunks.mjs',guavajam,args.chrome.resolve(),data/'segmented.sqlite',
             cases,out/f'chunks-{args.editions}.json','local,controlled','transaction,absentmatchingwordxyz,contract'])


if __name__ == '__main__':
    main()
