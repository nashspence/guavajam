"""Materialize bounded regular Git blobs without archive attributes or checkout filters."""
from contextlib import contextmanager
from pathlib import Path
import re,subprocess,tempfile
from asset_policy import AssetError


def git(repo,*args):
    result=subprocess.run(['git','--no-replace-objects',*args],cwd=repo,capture_output=True,timeout=90,check=False)
    if result.returncode:raise AssetError('Git object operation failed; details omitted')
    return result.stdout


@contextmanager
def snapshot(repo,revision):
    if not re.fullmatch('[0-9a-f]{40}',revision):raise AssetError('snapshot requires a full commit identity')
    with tempfile.TemporaryDirectory(prefix='press-source-') as directory:
        root=Path(directory);files=[];total=0
        for row in git(repo,'ls-tree','-r','-z',revision).split(b'\0'):
            if not row:continue
            fields,name=row.split(b'\t',1);mode,kind,oid=fields.split()
            if mode not in {b'100644',b'100755'} or kind!=b'blob':raise AssetError('source tree must contain regular files only')
            relative=Path(name.decode('utf-8','strict'))
            if relative.is_absolute() or '..' in relative.parts:raise AssetError('unsafe source object path')
            size=int(git(repo,'cat-file','-s',oid.decode()).strip());total+=size
            if size>20*1024*1024 or total>256*1024*1024:raise AssetError('source Git snapshot exceeds byte budget')
            destination=root/relative;destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(git(repo,'cat-file','blob',oid.decode()));files.append(relative)
        yield root,files
