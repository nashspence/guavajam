"""Generate disposable, actually signed publisher history; never production keys."""
from pathlib import Path
import json
import os
import subprocess


def command(repo, *args, env=None):
    result = subprocess.run(args, cwd=repo, env=env, text=True, capture_output=True, check=True)
    return result.stdout.strip()


class Publisher:
    def __init__(self, directory):
        self.root = Path(directory)
        self.repo = self.root / 'source'; self.repo.mkdir()
        self.home = self.root / 'keys'; self.home.mkdir(mode=0o700)
        self.env = {**os.environ, 'GNUPGHOME': str(self.home)}
        for args in [('init', '-q', '-b', 'main'), ('config', 'user.name', 'Independent Publisher'),
                     ('config', 'user.email', 'publisher@example.org'), ('config', 'gpg.format', 'openpgp')]:
            self.git(*args)
        command(self.repo, 'gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase', '',
                '--quick-generate-key', 'Discovery Fixture <publisher@example.org>', 'ed25519', 'sign', '0', env=self.env)
        listing = command(self.repo, 'gpg', '--with-colons', '--list-secret-keys', env=self.env)
        self.fingerprint = next(row.split(':')[9] for row in listing.splitlines() if row.startswith('fpr:'))
        self.git('config', 'user.signingkey', self.fingerprint)
        self.settings = {'schema': 'press.site.v1', 'publisher': 'Independent Publisher', 'base_url': 'https://publisher.example'}
        self.source('field-notes', 'Field Notes', 'The first observation.')
        self.write_settings()
        self.git('add', '-A'); self.git('commit', '-qm', 'Review source and site settings')
        self.seal()
        self.revisions = []

    def git(self, *args):
        return command(self.repo, 'git', *args, env=self.env)

    def source(self, slug, title, body):
        directory = self.repo / 'works' / slug; directory.mkdir(parents=True, exist_ok=True)
        (directory/'manuscript.md').write_text(f'''---
schema: press.work.v1
title: {title}
author: Example Author
slug: {slug}
language: en
status: draft
rights: CC-BY-4.0
---

# {title}

<a id="opening"></a>
## Opening

{body}
''')

    def write_settings(self):
        (self.repo / 'press-site.json').write_text(json.dumps(self.settings, sort_keys=True) + '\n')

    def seal(self, signed=True):
        self.git('commit', '--allow-empty', *(['-S'] if signed else []), '-qm', 'Approve site settings\n\nPress-Site: 1')
        return self.git('rev-parse', 'HEAD')

    def amend_settings(self, signed=True):
        self.write_settings(); self.git('add', 'press-site.json'); self.git('commit', '-qm', 'Review site settings')
        return self.seal(signed=signed)

    def publish(self, day='2026-11-13', slug='field-notes', label=None):
        from guavajam_core.publication_protocol import publication_trailers
        trailers = publication_trailers('works/' + slug + '/manuscript.md', day, label)
        self.git('commit', '--allow-empty', '-S', '-qm', 'Publish work\n\n' + '\n'.join(trailers))
        sha = self.git('rev-parse', 'HEAD'); self.revisions.append(sha)
        return sha
