"""Use caller-installed verification tools, never programs selected by a source repository."""
from pathlib import Path
import shutil
import re
import subprocess
from .asset_policy import AssetError


def verifier_arguments(repo,format):
    name='gpg' if format=='openpgp' else 'ssh-keygen'
    located=shutil.which(name)
    if not located:raise AssetError('required signature verifier is not installed: '+name)
    program=Path(located).resolve()
    if program.is_relative_to(Path(repo).resolve()):raise AssetError('signature verifier cannot come from the source repository')
    if format=='openpgp':return ['-c','gpg.format=openpgp','-c','gpg.program='+str(program),'-c','gpg.openpgp.program='+str(program)]
    return ['-c','gpg.format=ssh','-c','gpg.ssh.program='+str(program),'-c','gpg.minTrustLevel=fully']


def verify_signature(repo, revision, *, fingerprints=(), allowed_signers=None):
    """Verify one immutable commit using exactly one independent trust policy."""
    repo = Path(repo).resolve()
    if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise AssetError('signature verification requires a full lowercase commit SHA-1')
    if bool(fingerprints) == bool(allowed_signers):
        raise AssetError('provide exactly one independent external GPG or SSH trust policy')
    if fingerprints:
        trusted = {value.upper() for value in fingerprints}
        if any(not re.fullmatch(r'(?:[0-9A-F]{40}|[0-9A-F]{64})', value) for value in trusted):
            raise AssetError('use full trusted GPG fingerprints')
        command = ['git', '--no-replace-objects', *verifier_arguments(repo, 'openpgp'),
                   'verify-commit', '--raw', revision]
    else:
        path = Path(allowed_signers).expanduser().resolve(strict=True)
        if path.is_relative_to(repo) or not path.is_file() or path.stat().st_size > 65536:
            raise AssetError('SSH allowed signers must be an external bounded authority file outside the publication repository')
        command = ['git', '--no-replace-objects', *verifier_arguments(repo, 'ssh'),
                   '-c', 'gpg.ssh.allowedSignersFile=' + str(path), 'verify-commit', revision]
    try:
        result = subprocess.run(command, cwd=repo, capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AssetError('signature verification unavailable') from exc
    if result.returncode or len(result.stdout) > 65536 or len(result.stderr) > 65536:
        raise AssetError('untrusted, invalid or unauthorized commit signature')
    if fingerprints:
        matches = re.findall(rb'^\[GNUPG:\] VALIDSIG ([0-9A-Fa-f]{40,64})\b', result.stderr, re.M)
        if len(matches) != 1 or matches[0].decode('ascii').upper() not in trusted:
            raise AssetError('untrusted signing fingerprint is not in the independent trust policy')
        return matches[0].decode('ascii').upper()
    return 'external-ssh-allowed-signers'
