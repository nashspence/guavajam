"""Disk-backed document, anchor and reference inventory for source validation."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import tempfile

from .source_ast import plain_text, walk


class SourceInventory:
    def __init__(self, database):
        self.database = database

    def add(self, path, ast, stable, *, manuscript=False):
        key = str(path)
        with self.database:
            self.database.execute('INSERT INTO documents VALUES (?,?)', (key, int(manuscript)))
            self.database.executemany('INSERT OR IGNORE INTO anchors VALUES (?,?,?)',
                                     ((key, name, 1) for name in stable))
            for node in walk(ast['blocks']):
                if node['t'] == 'Header':
                    self.database.execute('INSERT OR IGNORE INTO anchors VALUES (?,?,0)', (key, node['c'][1][0]))
                elif node['t'] in {'Link', 'Image'}:
                    self.database.execute('INSERT INTO refs(path,kind,target,has_alt) VALUES (?,?,?,?)',
                        (key, node['t'], node['c'][2][0], int(bool(plain_text(node['c'][1]).strip()))))

    def contains(self, path):
        return self.database.execute('SELECT 1 FROM documents WHERE path=?', (str(path),)).fetchone() is not None

    def has_anchor(self, path, anchor, *, stable_only=False):
        return self.database.execute('SELECT 1 FROM anchors WHERE path=? AND anchor=?' +
            (' AND stable=1' if stable_only else ''), (str(path), anchor)).fetchone() is not None

    def references(self):
        for path, manuscript, kind, target, has_alt in self.database.execute(
                'SELECT r.path,d.manuscript,r.kind,r.target,r.has_alt FROM refs r '
                'JOIN documents d ON d.path=r.path ORDER BY r.path,r.id'):
            yield Path(path), bool(manuscript), kind, target, bool(has_alt)


@contextmanager
def source_inventory():
    with tempfile.TemporaryDirectory(prefix='guavajam-links-') as directory:
        with sqlite3.connect(Path(directory) / 'inventory.sqlite') as database:
            database.executescript("""
                PRAGMA cache_size=-2048;
                PRAGMA temp_store=FILE;
                CREATE TABLE documents (path TEXT PRIMARY KEY, manuscript INTEGER NOT NULL);
                CREATE TABLE anchors (path TEXT,anchor TEXT,stable INTEGER,PRIMARY KEY(path,anchor));
                CREATE TABLE refs (id INTEGER PRIMARY KEY,path TEXT,kind TEXT,target TEXT,has_alt INTEGER);
            """)
            yield SourceInventory(database)
