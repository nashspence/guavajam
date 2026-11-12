"""Use caller-installed verification tools, never programs selected by a source repository."""
from pathlib import Path
import shutil
from asset_policy import AssetError


def verifier_arguments(repo,format):
    name='gpg' if format=='openpgp' else 'ssh-keygen'
    located=shutil.which(name)
    if not located:raise AssetError('required signature verifier is not installed: '+name)
    program=Path(located).resolve()
    if program.is_relative_to(Path(repo).resolve()):raise AssetError('signature verifier cannot come from the source repository')
    if format=='openpgp':return ['-c','gpg.format=openpgp','-c','gpg.program='+str(program),'-c','gpg.openpgp.program='+str(program)]
    return ['-c','gpg.format=ssh','-c','gpg.ssh.program='+str(program),'-c','gpg.minTrustLevel=fully']
