#!/usr/bin/env python3
"""Exercise actual validator and generator enumeration with only guards removed."""
import argparse
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
BASE = Path(os.environ.get('GUAVAJAM_REPO', HERE.parents[1] / 'repos/guavajam')).resolve()
PANDOC = Path(os.environ.get('GUAVAJAM_PANDOC', HERE.parents[1] / 'tools/pandoc/bin/pandoc')).resolve()


def worker(mode, case):
    source = BASE if mode == 'stock' else HERE / 'candidate'
    sys.path[:0] = [str(source/'src'), str(source/'some-implementations/a-guavajam-static-website-generator/src')]
    os.environ['PATH'] = str(PANDOC.parent) + os.pathsep + os.environ['PATH']
    from guavajam.validate_sources import validate_repository
    from a_guavajam_static_website_generator.renderer import _raw_git_list
    result = {'mode': mode, 'case': case, 'python': sys.version, 'pandoc': subprocess.check_output([str(PANDOC), '--version'], text=True).splitlines()[0], 'source_module': str(sys.modules[validate_repository.__module__].__file__)}
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        support = root/'content/supporting'
        support.mkdir(parents=True)
        manuscript = root/'content/manuscript.md'
        manuscript.write_text('---\nschema: guavajam.work.v1\ntitle: Experiment\nauthor: Example Author\nlanguage: en\nrights: CC-BY-4.0\n---\n\n<a id="opening"></a>\n## Opening\n\nA generic work.\n')
        count = {'129-documents':129, '4000-documents':4000, 'over-100mib':21}.get(case, 1)
        for i in range(count):
            body = 'A generic supporting document.\n'
            if case == 'over-100mib':
                body = '```text\n' + ('x' * (5*1024*1024)) + '\n```\n'
            (support/f'page-{i:05}.md').write_text(f'---\ntitle: Observation {i}\n---\n\n<a name="observation"></a>\n\n' + body)
        first = support/'page-00000.md'
        if case == 'unanchored':first.write_text('---\ntitle: Broken\n---\n\nUnanchored paragraph\n')
        if case == 'bad-metadata':first.write_text('---\ntitle: Broken\nmetadata:\n  Nested:\n    inner: value\n---\n\n<a name="observation"></a>\n\nText\n')
        if case == 'bad-local-link':first.write_text('---\ntitle: Broken\n---\n\n<a name="observation"></a>\n\n[Bad](missing.md#absent)\n')
        if case == 'oversized-item':first.write_bytes(b'x' * (8*1024*1024+1))
        if case == 'symlink':
            first.unlink();first.symlink_to('../manuscript.md')
        paths = [Path('content/manuscript.md')] + [Path('content/supporting')/f'page-{i:05}.md' for i in range(count)]
        result['source_bytes'] = sum((root/p).stat().st_size for p in paths)
        start = time.perf_counter()
        errors = validate_repository(root, paths, pandoc=str(PANDOC), enforce_version=True)
        result.update(validation_seconds=time.perf_counter()-start, errors=errors, supporting_documents=count)
        if case in ('129-documents','4000-documents'):
            for args in (['init','-q'], ['config','user.name','Fixture'], ['config','user.email','fixture@example.test'], ['add','content'], ['commit','-qm','Fixture']):
                subprocess.run(['git',*args],cwd=root,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
            try:
                result['renderer_enumerated']=len(_raw_git_list(root,revision,'content/supporting'))
            except Exception as exc:result['renderer_error']=str(exc)
    result['peak_rss_kib']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(json.dumps(result),flush=True)


def run():
    results=[]
    cases=['129-documents','over-100mib','unanchored','bad-metadata','bad-local-link','oversized-item','symlink']
    for case in cases:
        for mode in ('stock','candidate'):
            done=subprocess.run([sys.executable,str(__file__),'--worker',mode,'--case',case],text=True,capture_output=True,timeout=300,check=True)
            row=json.loads(done.stdout);results.append(row)
            (HERE/'guard-results.json').write_text(json.dumps({'measurements':results},indent=2)+'\n')
            print(json.dumps(row),flush=True)
    for case in cases:
        before,after=[x for x in results if x['case']==case]
        if case in ('129-documents','over-100mib'):
            assert before['errors'] == ['content: supporting inventory exceeds 128 documents or 100 MiB'],before
            assert after['errors'] == [],after
        else:assert before['errors'] == after['errors'] and after['errors'],(before,after)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--worker');parser.add_argument('--case');args=parser.parse_args()
    worker(args.worker,args.case) if args.worker else run()
