"""Launch the optional GuavaJam *whole site* preview from a publication checkout.

Only the signed-main baseline and exact committed author branch are read.
Docker runs an independently pinned GuavaJam image with read-only source,
external public signer trust and read-only private S3 access; no source program
runs with either credential. No Docker socket is mounted inside the container.
"""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import tempfile

from .asset_policy import AssetError
from .identifiers import SLUG

SHA = re.compile(r'[a-f0-9]{40}\Z')
DIGEST_IMAGE = re.compile(r'[A-Za-z0-9._:/-]+@sha256:[a-f0-9]{64}\Z')


def command(*args, cwd=None, check=True):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=60, check=False)
    if check and result.returncode:
        raise AssetError('Git or Docker preparation failed; details withheld')
    return result


def source_ref(repo):
    name = command('git', 'symbolic-ref', '--quiet', '--short', 'HEAD', cwd=repo).stdout.strip()
    if not name or not re.fullmatch(r'[A-Za-z0-9._/-]+', name) or '..' in name:
        raise AssetError('preview requires a named committed authoring branch')
    return 'refs/heads/' + name


def image():
    selected = os.environ.get('GUAVAJAM_PREVIEW_IMAGE')
    if selected:
        if not DIGEST_IMAGE.fullmatch(selected):
            raise AssetError('operator preview image must be digest pinned')
        return selected, None
    try:
        distribution = importlib.metadata.distribution('guavajam-core')
        source = json.loads(distribution.read_text('direct_url.json'))
        sha = source['vcs_info']['commit_id']
        if (source['url'].rstrip('/').removesuffix('.git') != 'https://github.com/nashspence/guavajam'
                or not SHA.fullmatch(sha) or source.get('subdirectory') != 'projects/core'):
            raise ValueError()
    except (ValueError, KeyError, AttributeError, TypeError, importlib.metadata.PackageNotFoundError):
        raise AssetError('preview requires the exact installed GuavaJam Git pin or a digest-pinned image') from None
    return 'guavajam-preview:sha-' + sha[:12], sha


def safe_file(repo, file, *, owner_only=False):
    candidate = Path(file).expanduser()
    if candidate.is_symlink():
        raise AssetError('private preview inputs cannot be symlinks')
    candidate = candidate.resolve(strict=True)
    if candidate.is_relative_to(repo) or not candidate.is_file():
        raise AssetError('preview trust/storage files must be regular files outside the source checkout')
    info = candidate.stat()
    if owner_only and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise AssetError('private asset credentials must be owner-only')
    return candidate


def run(repo, work, published, *, label=None, port=8082):
    repo = Path(repo).expanduser().resolve(strict=True)
    if not SLUG.fullmatch(work) or not 1 <= port <= 65535:
        raise AssetError('select a valid work slug and preview port')
    ref = source_ref(repo)
    selected, pin = image()
    signer = os.environ.get('GUAVAJAM_SSH_ALLOWED_SIGNERS') or os.environ.get('SSH_ALLOWED_SIGNERS')
    gpg = os.environ.get('GUAVAJAM_TRUSTED_GPG_FINGERPRINTS') or os.environ.get('TRUSTED_GPG_FINGERPRINT')
    if bool(signer) == bool(gpg):
        raise AssetError('configure exactly one external signer trust source')
    if gpg and not re.fullmatch(r'(?:[0-9A-Fa-f]{40}|[0-9A-Fa-f]{64})(?:\s+(?:[0-9A-Fa-f]{40}|[0-9A-Fa-f]{64}))*', gpg):
        raise AssetError('provide full trusted GPG fingerprints')
    trust = safe_file(repo, signer or os.environ.get('GUAVAJAM_GPG_PUBLIC_KEYS', ''))
    private_env = os.environ.get('S3_ENV')
    if private_env:
        private_env = safe_file(repo, private_env, owner_only=True)
    # Compose/service require an external file even for asset-free drafts.
    with tempfile.TemporaryDirectory(prefix='guavajam-preview-') as temporary:
        state = Path(temporary)
        key = state/'session.key'
        token = secrets.token_hex(32)
        key.write_text(token);key.chmod(0o600)
        if private_env is None:
            private_env = state/'readonly.env'
            private_env.write_text('# No staged assets selected\n');private_env.chmod(0o600)
        env = ['-e', 'GUAVAJAM_PREVIEW_REPO=/source',
               '-e', 'GUAVAJAM_PREVIEW_REF='+ref,
               '-e', 'GUAVAJAM_PREVIEW_BASE_REF=refs/heads/main',
               '-e', 'GUAVAJAM_PREVIEW_WORK='+work,
               '-e', 'GUAVAJAM_PREVIEW_ORIGIN=http://127.0.0.1:'+str(port),
               '-e', 'GUAVAJAM_PREVIEW_TOKEN_FILE=/run/secrets/session.key',
               '-e', 'S3_ENV=/run/secrets/readonly.env']
        if published:
            env += ['-e', 'GUAVAJAM_PREVIEW_PUBLISHED='+published]
        if label:
            env += ['-e', 'GUAVAJAM_PREVIEW_LABEL='+label]
        if signer:
            env += ['-e', 'GUAVAJAM_SSH_ALLOWED_SIGNERS=/trust/allowed_signers']
            trusted_destination = '/trust/allowed_signers'
        else:
            env += ['-e', 'GUAVAJAM_SSH_ALLOWED_SIGNERS=',
                    '-e', 'GUAVAJAM_TRUSTED_GPG_FINGERPRINTS='+gpg,
                    '-e', 'GUAVAJAM_GPG_PUBLIC_KEYS=/trust/publisher.asc']
            trusted_destination = '/trust/publisher.asc'
        if pin and command('docker', 'image', 'inspect', selected, check=False).returncode:
            # Build only the exact source revision pinned by the installed core;
            # never select an unreviewed feature branch or floating latest image.
            subprocess.run(['docker', 'build', '--target', 'preview', '--tag', selected,
                            'https://github.com/nashspence/guavajam.git?ref='+pin], check=True)
        mounts = []
        for path, destination in [(repo, '/source'), (private_env, '/run/secrets/readonly.env'),
                                  (key, '/run/secrets/session.key'), (trust, trusted_destination)]:
            mounts.extend(['--mount', f'type=bind,src={path},dst={destination},readonly'])
        argv = ['docker', 'run', '--rm', '--init', '--read-only', '--cap-drop', 'ALL',
                '--security-opt', 'no-new-privileges', '--user', str(os.getuid())+':'+str(os.getgid()),
                '--tmpfs', '/tmp', '--mount', f'type=bind,src={state},dst=/state',
                '--publish', '127.0.0.1:'+str(port)+':8080', *mounts, *env, selected]
        print('GuavaJam complete private site preview:', flush=True)
        print('http://127.0.0.1:'+str(port)+'/_session?token='+token, flush=True)
        print('Source: '+ref+' (committed snapshot). Ctrl+C stops preview. No public release.', flush=True)
        return subprocess.run(argv, check=False).returncode


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default='.')
    parser.add_argument('--work', required=True)
    parser.add_argument('--published')
    parser.add_argument('--label')
    parser.add_argument('--port', type=int, default=8082)
    args=parser.parse_args(argv)
    try:
        return run(args.repo, args.work, args.published, label=args.label, port=args.port)
    except (AssetError, OSError, subprocess.SubprocessError):
        print('Unable to start private preview. Check Docker, independent signer trust and private read credentials.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
