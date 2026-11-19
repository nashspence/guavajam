"""Disposable one-work repositories with real publisher signatures."""
from pathlib import Path
import os
import subprocess


def command(repo, *args, env=None):
    result = subprocess.run(args, cwd=repo, env=env, text=True, capture_output=True, check=True)
    return result.stdout.strip()


class Draft:
    """A local authoring branch needs no signer setup or publication history."""
    def __init__(self, directory, *, branch='draft', commit=True):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True)
        self.repo = self.root / 'source'
        self.repo.mkdir()
        self.env = {**os.environ, 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_SYSTEM': '/dev/null'}
        for args in [('init', '-q', '-b', branch), ('config', 'user.name', 'Independent Publisher'),
                     ('config', 'user.email', 'publisher@example.org')]:
            self.git(*args)
        (self.repo / 'README.md').write_text('Authoring repository.\n')
        self.git('add', 'README.md')
        self.git('commit', '-qm', 'Initialize authoring tools')
        self.source('Field Notes', 'The first observation.')
        if commit:
            self.git('add', 'content')
            self.git('commit', '-qm', 'Write an unpublished draft')

    def git(self, *args, env=None):
        return command(self.repo, 'git', *args, env=self.env if env is None else {**self.env, **env})

    def source(self, title, body):
        directory = self.repo / 'content'
        directory.mkdir(exist_ok=True)
        (directory / 'manuscript.md').write_text(f'''---
schema: guavajam.work.v1
title: {title}
author: Example Author
language: en
rights: CC-BY-4.0
---

# {title}

<a id="opening"></a>
## Opening

{body}
''')


class Publisher(Draft):
    """An independent SSH identity, outside the signed repository under test."""
    def __init__(self, directory):
        super().__init__(directory, branch='main', commit=False)
        self.home = self.root / 'keys'
        self.home.mkdir(mode=0o700)
        self.key = self.home / 'publisher'
        command(self.repo, 'ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(self.key), env=self.env)
        self.allowed_signers = self.home / 'allowed-signers'
        self.allowed_signers.write_text('publisher namespaces="git" ' + self.key.with_suffix('.pub').read_text())
        self.allowed_signers.chmod(0o600)
        self.trust = {'allowed_signers': self.allowed_signers}
        self.git('config', 'gpg.format', 'ssh')
        self.git('config', 'user.signingkey', str(self.key))
        self.revisions = []
        self.publish('Initial edition')

    def publish(self, message='Revise the work', *, date=None):
        self.git('add', 'content')
        env = {'GIT_AUTHOR_DATE': date, 'GIT_COMMITTER_DATE': date} if date else None
        self.git('commit', '-S', '-qm', message, env=env)
        sha = self.git('rev-parse', 'HEAD')
        self.revisions.append(sha)
        return sha
