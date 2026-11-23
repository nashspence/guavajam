"""Run the archive-to-review handoff with isolated installed distributions."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
from workspace import ROOT, EXTRACTOR_NAME
from version import check

def run(*args,**kwargs): subprocess.run(args,check=True,**kwargs)

if __name__ == '__main__':
    version=check()
    for name in ['guavajam',EXTRACTOR_NAME]: run('uv','build','--package',name,cwd=ROOT)
    with tempfile.TemporaryDirectory(prefix='chatgpt-handoff-installed-') as scratch:
        root=Path(scratch);environment=root/'environment'
        env={k:v for k,v in os.environ.items() if k not in {'PYTHONPATH','VIRTUAL_ENV','UV_PROJECT_ENVIRONMENT'}}
        run('uv','venv','--python',sys.executable,str(environment),env=env)
        python=environment/'bin/python'
        wheels=[str(ROOT/'dist'/(name.replace('-','_')+'-'+version+'-py3-none-any.whl')) for name in ['guavajam',EXTRACTOR_NAME]]
        run('uv','pip','install','--python',str(python),*wheels,env=env)
        run(str(python),'-I',str(ROOT/'tests/integration/run_chatgpt_archive.py'),cwd=root,env=env)
