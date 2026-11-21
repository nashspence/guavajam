"""Create owner-only identities for the optional local S3 service; never overwrite."""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import tempfile
from .s3 import S3Config


def role_actions(bucket):
    """Object-prefix capabilities; no staging writer can forge release authority.

    SeaweedFS object-scoped actions: S3-Credentials / Object-Level Permissions.
    The real Compose contract also attempts forbidden raw S3 writes, bypassing
    our client-mode checks, to qualify provider enforcement.
    """
    return {
        'publisher': ['Read:' + bucket + '/sha256/*', 'List:' + bucket, 'Write:' + bucket + '/sha256/*'],
        'delivery': ['Read:' + bucket],
        'release': ['Read:' + bucket + '/released/sha256/*', 'List:' + bucket, 'Write:' + bucket + '/released/sha256/*'],
        'maintenance': ['Read:' + bucket, 'List:' + bucket, 'Write:' + bucket],
    }


def initialize(directory, bucket='publication-assets'):
    S3Config('https://storage.example', 'us-east-1', bucket, 'validation', 'validation')
    directory = Path(directory).absolute()
    directory.parent.mkdir(parents=True, exist_ok=True)
    if directory.exists() or directory.is_symlink():
        raise ValueError('configuration already exists; it will not be overwritten')
    pending = Path(tempfile.mkdtemp(prefix='.local-assets-', dir=directory.parent))
    try:
        identities = []
        for role, actions in role_actions(bucket).items():
            access, secret = secrets.token_hex(16), secrets.token_hex(32)
            identities.append({'name': role, 'credentials': [{'accessKey': access, 'secretKey': secret}],
                               'actions': actions})
            for name, value in [(role + '-access-key', access), (role + '-secret-key', secret)]:
                with os.fdopen(os.open(pending / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
                    stream.write(value + '\n')
        path = pending / 's3-identities.json'
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
            json.dump({'identities': identities}, stream)
            stream.write('\n')
        pending.rename(directory)
    finally:
        if pending.exists():shutil.rmtree(pending)
    return directory


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--bucket', default='publication-assets')
    args = parser.parse_args(argv)
    try:
        initialize(args.directory, args.bucket)
    except (OSError, ValueError):
        print('Local asset configuration could not be created; existing files are preserved')
        return 1
    print('Created separate staging, delivery, release and maintenance identities; no credentials printed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
