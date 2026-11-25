#!/usr/bin/env python3
"""Extract a complete title/abstract/facet directory, preserving its FTS corpus.

This partitions by search surface, not by software version or local relevance
scores. Every original document occurrence and document_fts row is retained.
The manifest/router integration is intentionally not implemented here.
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path
from storage_matrix import describe

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('database',type=Path)
    p.add_argument('output',type=Path)
    args=p.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    source=sqlite3.connect(args.database)
    target=sqlite3.connect(args.output)
    source.backup(target)
    target.execute('PRAGMA foreign_keys=OFF')
    for name in ('search_fts','passage_fts','passage_records','passages','search_records','edition_search_fields'):
        target.execute('DROP TABLE '+name)
    target.commit();target.execute('VACUUM')
    assert target.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert not target.execute('PRAGMA foreign_key_check').fetchall()
    for table in ('documents','document_fts','document_facets','facet_values'):
        assert source.execute('SELECT * FROM '+table).fetchall()==target.execute('SELECT * FROM '+table).fetchall()
    source.close();target.close()
    result=describe(args.output)
    result.update({'kind':'document directory only','source':str(args.database),
                   'parity':'all original document rows, FTS rows and facet memberships exactly equal',
                   'limitations':'routing/manifest/frontend not implemented; not a full work index'})
    args.output.with_suffix('.report.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='table_bytes'}))

if __name__=='__main__':main()
