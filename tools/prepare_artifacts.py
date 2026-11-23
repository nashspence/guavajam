"""Prepare selected independently owned artifacts without tags or releases."""
from pathlib import Path
import argparse
import hashlib
import shutil
import subprocess
import sys
from workspace import ROOT, PROJECTS, PYTHON_PROJECTS, ARCHIVER_NAME
from version import check

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package',choices=['all',*PROJECTS],default='all')
    parser.add_argument('--version',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();version=check()
    if version != args.version: parser.error('requested version differs from committed metadata')
    selected=list(PROJECTS) if args.package=='all' else [args.package]
    python=[name for name in selected if name in PYTHON_PROJECTS]
    build=set(python)
    if python: build.add('guavajam')
    for name in sorted(build): subprocess.run(['uv','build','--package',name],cwd=ROOT,check=True)
    for name in python: subprocess.run([sys.executable,str(ROOT/'tools/check_artifacts.py'),'--package',name],cwd=ROOT,check=True)
    if ARCHIVER_NAME in selected:
        subprocess.run([sys.executable,str(PROJECTS[ARCHIVER_NAME]/'tools/build.py'),'--output',str(ROOT/'dist'/(ARCHIVER_NAME+'-'+version+'.zip'))],check=True)
    args.output.mkdir(parents=True,exist_ok=False)
    for name in selected:
        stem=name.replace('-','_') if name in PYTHON_PROJECTS else name
        suffixes=['.whl','.tar.gz'] if name in PYTHON_PROJECTS else ['.zip']
        artifacts=[p for p in (ROOT/'dist').glob(stem+'-'+version+'*') if any(p.name.endswith(s) for s in suffixes)]
        assert len(artifacts)==len(suffixes),'missing selected artifact'
        for artifact in artifacts: shutil.copyfile(artifact,args.output/artifact.name)
    records=[hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name for p in sorted(args.output.iterdir())]
    (args.output/'SHA256SUMS').write_text('\n'.join(records)+'\n')
