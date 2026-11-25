#!/usr/bin/env python3
"""Export three fixed transport cases with a read-only baseline SQL oracle."""
import argparse
import json
from pathlib import Path
import sqlite3
from urllib.parse import quote
from query_pipeline import query_case

parser=argparse.ArgumentParser()
parser.add_argument('--database',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
database=args.database.resolve(strict=True)
connection=sqlite3.connect('file:'+quote(str(database),safe='/')+'?mode=ro&immutable=1',uri=True)
connection.row_factory=sqlite3.Row
connection.execute('PRAGMA query_only=ON')
cases=[]
try:
    for term in ('contract','transaction','absentmatchingwordxyz'):
        case=query_case(kind='passages',term=term,edition='all',sort='relevance',page=1)
        count=case['baseline_count'];page=case['baseline_page']
        case['expected_count']=connection.execute(count['sql'],count['bind']).fetchone()[0]
        case['expected_rows']=[dict(row) for row in connection.execute(page['sql'],page['bind'])]
        cases.append(case)
        print(json.dumps({'term':term,'count':case['expected_count'],'page_rows':len(case['expected_rows'])}),flush=True)
finally:
    connection.close()
args.output.parent.mkdir(parents=True,exist_ok=True)
args.output.write_text(json.dumps(cases,ensure_ascii=False,indent=2)+'\n')
