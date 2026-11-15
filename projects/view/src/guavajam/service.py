"""Environment-configured periodic view service; all authority stays in update()."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import threading
import time
from urllib.parse import urlsplit
import uuid

from .update import atomic, update


def command(args, **kwargs):
    result = subprocess.run(args, capture_output=True, timeout=300, **kwargs)
    if result.returncode:
        raise ValueError('service command failed; transport and response details omitted')
    return result.stdout


def source_url(value):
    if not value or len(value) > 4096 or any(ord(c) < 33 for c in value):
        raise ValueError('configure a source repository URL')
    parsed = urlsplit(value)
    if parsed.scheme in {'https', 'ssh'}:
        if not parsed.hostname or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('invalid source URL')
        if parsed.scheme == 'https' and parsed.username:
            raise ValueError('use mounted transport credentials, never credentials in HTTPS URLs')
    elif parsed.scheme == 'file':
        if parsed.netloc or not parsed.path.startswith('/') or parsed.query or parsed.fragment:
            raise ValueError('file source requires an absolute operator-mounted repository')
    elif not re.fullmatch(r'[a-zA-Z0-9_.-]+@[a-zA-Z0-9.-]+:[a-zA-Z0-9_./-]+', value):
        raise ValueError('source must use HTTPS, SSH or an operator-mounted file repository')
    return value


@contextmanager
def service_lock(state):
    state.mkdir(parents=True, exist_ok=True)
    if state.resolve() != state:
        raise ValueError('service state must not have symlink ancestors')
    fd = os.open(state / '.service.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def prepare_source(state, url, ref):
    url = source_url(url)
    if not re.fullmatch(r'refs/heads/[a-zA-Z0-9._/-]+', ref) or '..' in ref:
        raise ValueError('configure a source branch ref')
    repo = state / 'source'
    if repo.is_symlink():
        raise ValueError('source clone must not be a symlink')
    if repo.exists():
        configured = command(['git', '-C', str(repo), 'remote', 'get-url', 'origin']).decode().strip()
        if configured != url:
            raise ValueError('source URL changed; use a separate state volume for another publisher')
        return repo
    pending = state / ('.source-' + uuid.uuid4().hex)
    try:
        command(['git', '-c', 'protocol.ext.allow=never', '-c', 'core.hooksPath=/dev/null',
                 'clone', '--no-checkout', '--single-branch', '--branch', ref[11:], '--', url, str(pending)])
        command(['git', '-C', str(pending), 'config', 'core.hooksPath', '/dev/null'])
        pending.rename(repo)
    finally:
        if pending.exists():
            shutil.rmtree(pending)
    return repo


def trust(state, repo, environ):
    fingerprints = environ.get('GUAVAJAM_TRUSTED_GPG_FINGERPRINTS', '').split()
    signers = environ.get('GUAVAJAM_SSH_ALLOWED_SIGNERS', '')
    if bool(fingerprints) == bool(signers):
        raise ValueError('configure exactly one independent GPG or SSH signer policy')
    if fingerprints:
        keys = Path(environ['GUAVAJAM_GPG_PUBLIC_KEYS']).resolve(strict=True)
        if not keys.is_file() or keys.stat().st_size > 1024 * 1024 or keys.is_relative_to(repo):
            raise ValueError('GPG public keys must be operator-provisioned outside source')
        home = state / 'gnupg'; home.mkdir(mode=0o700, exist_ok=True)
        os.environ['GNUPGHOME'] = str(home)
        command(['gpg', '--batch', '--import', str(keys)])
        return {'fingerprints': fingerprints}
    path = Path(signers).resolve(strict=True)
    if path.is_relative_to(repo):
        raise ValueError('SSH signer policy must be outside source')
    return {'allowed_signers': path}


class ServiceError(ValueError):
    def __init__(self, stage):
        super().__init__('view service step failed: ' + stage)
        self.stage = stage


def poll(state, environ):
    ref = environ.get('GUAVAJAM_SOURCE_REF', 'refs/heads/main')
    stage = 'source'
    try:
        repo = prepare_source(state, environ.get('GUAVAJAM_SOURCE_URL', ''), ref)
        stage = 'trust'
        policy = trust(state, repo, environ)
        stage = 'update'
        return update(repo, state / 'site', ref=ref, **policy)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        raise ServiceError(stage) from None


def healthy(state, interval):
    try:
        value = json.loads((state / 'health.json').read_text())
        return (value['schema'] == 'guavajam.service.v1' and value['passed'] is True
                and 0 <= time.time() - value['checked_at'] <= interval + 600
                and (state / 'site/current/index.html').is_file())
    except (OSError, ValueError, KeyError, TypeError):
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--health', action='store_true')
    args = parser.parse_args(argv)
    try:
        state = Path(os.environ.get('GUAVAJAM_STATE', '/state')).absolute()
        interval = int(os.environ.get('GUAVAJAM_POLL_SECONDS', '300'))
        if not 5 <= interval <= 86400:
            raise ValueError('poll interval must be 5..86400 seconds')
        if args.health:
            return 0 if healthy(state, interval) else 1
        stopping = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stopping.set())
        with service_lock(state):
            while not stopping.is_set():
                try:
                    result = poll(state, os.environ)
                    passed = result['status'] in {'updated', 'unchanged'}
                    status = result['status']; stage = 'complete'
                except ServiceError as exc:
                    passed, status, stage = False, 'failed', exc.stage
                atomic(state / 'health.json', json.dumps({'schema': 'guavajam.service.v1',
                       'passed': passed, 'checked_at': time.time(), 'status': status, 'stage': stage}).encode())
                print(json.dumps({'status': status, 'passed': passed, 'stage': stage}), flush=True)
                if args.once:
                    return 0 if passed else 1
                stopping.wait(interval)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        print('GuavaJam service configuration or state is invalid; details omitted', flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
