"""Host an authenticated private preview of an operator-selected local Git branch.

Source and storage are read-only. Candidate programs, hooks and build files never run.
The operator owns the checkout, selection, TLS ingress and a mode-0600 session key.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import threading
import time
import urllib.request
from urllib.parse import urlsplit
import uuid

from guavajam_core.asset_storage import credentials
from guavajam_core.publication_protocol import resolve
from .preview import build_preview, start_server, preview_refs
from .service import service_lock, trust
from .update import atomic


def settings(env):
    origin = env['GUAVAJAM_PREVIEW_ORIGIN']
    url = urlsplit(origin)
    local = url.scheme == 'http' and url.hostname == '127.0.0.1'
    if ((url.scheme != 'https' and not local) or not url.hostname or url.username or url.password
            or origin != url.scheme + '://' + url.netloc):
        raise ValueError('HTTPS or localhost preview origin required')
    port = int(env.get('GUAVAJAM_PREVIEW_PORT', '8080'))
    interval = int(env.get('GUAVAJAM_POLL_SECONDS', '30'))
    if not 1 <= port <= 65535 or not 5 <= interval <= 86400: raise ValueError('invalid preview timing')
    return origin, port, interval


def session_key(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 128): raise ValueError('unsafe session key')
        token = stream.read(129).decode().strip()
    if not re.fullmatch('[0-9a-f]{64}', token): raise ValueError('invalid session key')
    return token


def reachable(origin, port, token):
    request = urllib.request.Request('http://127.0.0.1:' + str(port) + '/',
                                    headers={'Host': urlsplit(origin).netloc, 'Cookie': 'guavajam_preview=' + token})
    with urllib.request.urlopen(request, timeout=2) as response:
        return response.status == 200 and response.getheader('Cache-Control') == 'private, no-store'


class Preview:
    def __init__(self, state, env):
        self.state, self.env = state, env
        self.origin, self.port, self.interval = settings(env)
        self.token = session_key(env['GUAVAJAM_PREVIEW_TOKEN_FILE'])
        self.repo = Path(env['GUAVAJAM_PREVIEW_REPO']).resolve(strict=True)
        self.ref, self.work = env['GUAVAJAM_PREVIEW_REF'], env['GUAVAJAM_PREVIEW_WORK']
        self.base_ref = env.get('GUAVAJAM_PREVIEW_BASE_REF') or None
        configured = any(env.get(key) for key in ('GUAVAJAM_SSH_ALLOWED_SIGNERS',
                         'GUAVAJAM_TRUSTED_GPG_FINGERPRINTS', 'GUAVAJAM_GPG_PUBLIC_KEYS'))
        self.policy = trust(state, self.repo, env) if configured else {}
        self.baseline_path = state / 'baseline.json'
        self.baseline = json.loads(self.baseline_path.read_text()) if self.baseline_path.exists() else None
        self.selection = None
        if not (re.fullmatch(r'refs/heads/[A-Za-z0-9._/-]+', self.ref) and '..' not in self.ref):
            raise ValueError('select an explicit local branch')
        self.process = self.output = self.revision = None

    def close(self):
        if self.process and self.process.poll() is None:
            self.process.terminate(); self.process.wait(timeout=15)
        self.process = None

    def refresh(self):
        revision = resolve(self.repo, self.ref)
        selection = (revision, tuple(preview_refs(self.repo).items()))
        if selection == self.selection and self.process and self.process.poll() is None:
            if not reachable(self.origin, self.port, self.token): raise ValueError('preview unavailable')
            return 'unchanged'
        pending = self.state / ('preview-' + uuid.uuid4().hex)
        try:
            manifest = build_preview(self.repo, revision, self.work, pending,
                                     base_ref=self.base_ref, baseline=self.baseline,
                                     require_retention=self.env.get('GUAVAJAM_REQUIRE_RETENTION') == '1',
                                     **self.policy)
            values = credentials(self.repo) if manifest['assets'] else {}
            previous = self.output
            self.close()
            self.process = start_server(pending, manifest, values, port=self.port, token=self.token,
                                        web_origin=self.origin, quiet=True)
            deadline = time.monotonic() + 15
            while True:
                if self.process.poll() is not None: raise ValueError('preview listener failed')
                try:
                    if reachable(self.origin, self.port, self.token): break
                except OSError: pass
                if time.monotonic() >= deadline: raise ValueError('preview listener timeout')
                time.sleep(0.1)
            if manifest['repository_checkpoint']:
                baseline = {'schema': 'guavajam.preview-baseline.v1',
                            'checkpoint': manifest['repository_checkpoint'], 'catalog': manifest['verified_catalog']}
                atomic(self.baseline_path, json.dumps(baseline).encode())
                self.baseline = baseline
            self.output, self.revision, self.selection = pending, revision, selection
            if previous: shutil.rmtree(previous)
            return 'updated'
        except BaseException:
            if pending != self.output and pending.exists(): shutil.rmtree(pending)
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--health', action='store_true')
    args = parser.parse_args(argv)
    try:
        state = Path(os.environ.get('GUAVAJAM_STATE', '/state')).absolute()
        origin, port, interval = settings(os.environ)
        if args.health:
            receipt = json.loads((state / 'health.json').read_text())
            token = session_key(os.environ['GUAVAJAM_PREVIEW_TOKEN_FILE'])
            return 0 if (receipt.get('schema') == 'guavajam.preview-service.v1' and receipt.get('passed') is True
                         and 0 <= time.time() - receipt['checked_at'] <= interval + 60 and reachable(origin, port, token)) else 1
        stopping = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT): signal.signal(sig, lambda *_: stopping.set())
        with service_lock(state):
            preview = Preview(state, os.environ)
            try:
                while not stopping.is_set():
                    try: status, passed = preview.refresh(), True
                    except Exception: status, passed = 'failed', False
                    receipt = {'schema': 'guavajam.preview-service.v1', 'passed': passed, 'status': status,
                               'checked_at': time.time(), 'revision': preview.revision, 'publication': False}
                    atomic(state / 'health.json', json.dumps(receipt).encode())
                    print(json.dumps({'passed': passed, 'status': status}), flush=True)
                    stopping.wait(interval)
            finally: preview.close()
    except Exception:
        print('Private preview service configuration invalid; details omitted', flush=True)
        return 1
    return 0


if __name__ == '__main__': raise SystemExit(main())
