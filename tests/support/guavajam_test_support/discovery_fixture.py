"""Generate disposable, actually signed publisher history; never production keys."""
from pathlib import Path
import json
import os
import re
import subprocess


def command(repo, *args, env=None):
    result = subprocess.run(args, cwd=repo, env=env, text=True, capture_output=True, check=True)
    return result.stdout.strip()


class Draft:
    """Fresh local repository; no keys, signer configuration, main or remote."""
    def __init__(self, directory, *, branch='draft', base_url=None):
        self.root = Path(directory); self.root.mkdir(parents=True, exist_ok=True)
        self.repo = self.root / 'source'; self.repo.mkdir()
        self.env = {**os.environ, 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_SYSTEM': '/dev/null'}
        for args in [('init', '-q', '-b', branch), ('config', 'user.name', 'Independent Publisher'),
                     ('config', 'user.email', 'publisher@example.org')]:
            self.git(*args)
        self.settings = {'schema': 'guavajam.repository.v1', 'publisher': 'Independent Publisher'}
        if base_url:
            self.settings['base_url'] = base_url
        self.source('field-notes', 'Field Notes', 'The first observation.')
        self.write_settings()
        self.git('add', '-A'); self.git('commit', '-qm', 'Review source and repository settings')

    def git(self, *args):
        return command(self.repo, 'git', *args, env=self.env)

    def source(self, slug, title, body):
        directory = self.repo / 'works' / slug; directory.mkdir(parents=True, exist_ok=True)
        manuscript = directory / 'manuscript.md'
        previous = re.search(r'^edition: [\"\']?([0-9]+)', manuscript.read_text(), re.M) if manuscript.exists() else None
        edition = previous[1] if previous else '0'
        manuscript.write_text(f'''---
schema: guavajam.work.v1
title: {title}
author: Example Author
slug: {slug}
language: en
edition: "{edition}"
rights: CC-BY-4.0
---

# {title}

<a id="opening"></a>
## Opening

{body}
''')

    def write_settings(self):
        (self.repo / 'guavajam.json').write_text(json.dumps(self.settings, sort_keys=True) + '\n')


class Publisher(Draft):
    def __init__(self, directory):
        super().__init__(directory, branch='main', base_url='https://publisher.example')
        self.home = self.root / 'keys'; self.home.mkdir(mode=0o700)
        self.env['GNUPGHOME'] = str(self.home)
        command(self.repo, 'gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase', '',
                '--quick-generate-key', 'Discovery Fixture <publisher@example.org>', 'ed25519', 'sign', '0', env=self.env)
        listing = command(self.repo, 'gpg', '--with-colons', '--list-secret-keys', env=self.env)
        self.fingerprint = next(row.split(':')[9] for row in listing.splitlines() if row.startswith('fpr:'))
        self.git('config', 'gpg.format', 'openpgp')
        self.git('config', 'user.signingkey', self.fingerprint)
        self.seal()
        self.revisions = []

    def seal(self, signed=True):
        self.git('commit', '--allow-empty', *(['-S'] if signed else []), '-qm', 'Approve repository settings')
        return self.git('rev-parse', 'HEAD')

    def amend_settings(self, signed=True):
        self.write_settings(); self.git('add', 'guavajam.json')
        self.git('commit', '--allow-empty', *(['-S'] if signed else []), '-qm', 'Review repository settings')
        return self.git('rev-parse', 'HEAD')

    def publish(self, day='2026-11-13', slug='field-notes', label=None):
        """Issue a real content/version commit; label is ordinary commit prose."""
        manuscript = self.repo / 'works' / slug / 'manuscript.md'
        source = manuscript.read_text()
        current = int(re.search(r'^edition: ["\']?([0-9]+)', source, re.M)[1])
        manuscript.write_text(re.sub(r'^edition: .*$', 'edition: "' + str(current + 1) + '"', source, flags=re.M))
        self.git('add', 'works/' + slug)
        original = self.env.get('GIT_COMMITTER_DATE')
        self.env['GIT_COMMITTER_DATE'] = day + 'T12:00:00+00:00'
        try:
            self.git('commit', '-S', '-qm', label if label is not None else 'Publish ' + slug)
        finally:
            if original is None:
                self.env.pop('GIT_COMMITTER_DATE', None)
            else:
                self.env['GIT_COMMITTER_DATE'] = original
        sha = self.git('rev-parse', 'HEAD'); self.revisions.append(sha)
        return sha
