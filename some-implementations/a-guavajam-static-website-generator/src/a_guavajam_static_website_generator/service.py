"""Environment-configured collection view service with independent publisher trust."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time

from .update import atomic


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


class ServiceError(ValueError):
    def __init__(self, stage):
        super().__init__('view service step failed: ' + stage)
        self.stage = stage


def poll(state, environ):
    from .index import update_index
    try:
        return update_index(environ['GUAVAJAM_INDEX_URL'], environ['GUAVAJAM_TRUST_FILE'], state / 'site',
                            site_origin=environ.get('GUAVAJAM_SITE_ORIGIN') or None)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        raise ServiceError('collection') from None


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
