#!/usr/bin/env python3
"""Observe immutable SQLite xRead demand, then model aligned HTTP ranges.

Native APSW tracing, not browser/WASM latency qualification. Every case/mode
starts with a new connection, 8 MiB SQLite cache, mmap off, temp_store=MEMORY.
Count then first page shares that connection. No main-database writes occur.
"""
import argparse
from collections import Counter, OrderedDict
import hashlib
import gzip
import json
from pathlib import Path
import sys
import time
from urllib.parse import quote
import uuid

import apsw


class BlockModel:
    def __init__(self, size, database_bytes, cache_bytes):
        self.size = size
        self.database_bytes = database_bytes
        self.capacity = max(1, cache_bytes // size)
        self.seen = set()
        self.lru = OrderedDict()
        self.unique_bytes = 0
        self.requests = 0
        self.transferred = 0

    def read(self, offset, amount):
        end = min(offset + amount, self.database_bytes)
        for block in range(offset // self.size, (end + self.size - 1) // self.size):
            size = min(self.size, self.database_bytes - block * self.size)
            if block not in self.seen:
                self.seen.add(block)
                self.unique_bytes += size
            if block in self.lru:
                self.lru.move_to_end(block)
            else:
                self.requests += 1
                self.transferred += size
                self.lru[block] = True
                if len(self.lru) > self.capacity:
                    self.lru.popitem(last=False)

    def output(self):
        return {'block_bytes': self.size, 'unique_blocks': len(self.seen), 'cold_unique_bytes': self.unique_bytes,
                'unlimited_cache_serial_40ms_10mbps_seconds': 0.04*len(self.seen) + 8*self.unique_bytes/10000000,
                'remote_cache_bytes': self.capacity*self.size, 'lru_requests': self.requests, 'lru_transfer_bytes': self.transferred,
                'lru_serial_40ms_10mbps_seconds': 0.04*self.requests + 8*self.transferred/10000000}


class Recorder:
    def __init__(self, size):
        self.size = size
        self.phase = 'open'
        self.calls = Counter()
        self.bytes = Counter()
        self.pages = set()
        self.models = [BlockModel(block, size, 8*1024*1024) for block in (65536,262144,1048576)]

    def read(self, amount, offset):
        self.calls[self.phase] += 1
        self.bytes[self.phase] += amount
        self.pages.update(range(offset//4096,(min(offset+amount,self.size)+4095)//4096))
        for model in self.models:
            model.read(offset, amount)

    def output(self):
        return {'xread_calls':sum(self.calls.values()),'xread_requested_bytes':sum(self.bytes.values()),
                'xread_calls_by_phase':dict(self.calls),'xread_bytes_by_phase':dict(self.bytes),
                'unique_4k_pages':len(self.pages),'unique_4k_page_bytes':sum(min(4096,self.size-page*4096) for page in self.pages),
                'range_models':[model.output() for model in self.models]}


class TraceFile(apsw.VFSFile):
    def __init__(self, base, name, flags, recorder):
        self.recorder = recorder if flags[0] & apsw.SQLITE_OPEN_MAIN_DB else None
        super().__init__(base, name, flags)

    def xRead(self, amount, offset):
        if self.recorder:
            self.recorder.read(amount, offset)
        return super().xRead(amount, offset)


class TraceVFS(apsw.VFS):
    def __init__(self, name, recorder):
        self.base = apsw.vfs_names()[0]
        self.recorder = recorder
        super().__init__(name, self.base)

    def xOpen(self, name, flags):
        return TraceFile(self.base, name, flags, self.recorder)


def trace(database, case, mode, maximum_seconds, recorder=None):
    recorder = recorder or Recorder(database.stat().st_size)
    name = 'guavajam-range-trace-' + uuid.uuid4().hex
    vfs = TraceVFS(name, recorder)
    start = time.monotonic()
    connection = apsw.Connection('file:' + quote(str(database),safe='/') + '?immutable=1',
                                 flags=apsw.SQLITE_OPEN_READONLY|apsw.SQLITE_OPEN_URI, vfs=name)
    try:
        connection.execute('PRAGMA mmap_size=0; PRAGMA cache_size=-8192; PRAGMA temp_store=MEMORY; PRAGMA query_only=ON;')
        connection.set_progress_handler(lambda:time.monotonic()-start>maximum_seconds,100000)
        outputs = {}
        timings = {}
        for operation in ('count','page'):
            query = case[mode+'_'+operation]
            recorder.phase = operation
            then = time.monotonic()
            outputs[operation] = list(connection.execute(query['sql'],query['bind']))
            timings[operation] = time.monotonic() - then
        summary = recorder.output()
        summary.update(mode=mode, count=outputs['count'][0][0], page_rows=len(outputs['page']),
                       native_traced_seconds=timings, result_sha256=hashlib.sha256(json.dumps(outputs,ensure_ascii=False,separators=(',',':')).encode()).hexdigest())
        return summary, outputs
    finally:
        connection.close()
        vfs.unregister()


def selected(case):
    key=(case['kind'],case['term'],case['edition'],case['page'])
    return key in {('documents','contract','all',1),('passages','contract','all',1),
                   ('passages','transaction','all',1),('passages','needle','all',1),
                   ('passages','absentmatchingwordxyz','all',1),('passages','contract','latest',1),
                   ('versions','contract','all',1),('collection','contract','all',1)}


def run(args):
    database = args.database.resolve(strict=True)
    cases = json.loads(args.cases.read_text())
    report = {'database':str(database),'database_bytes':database.stat().st_size,
              'database_sha256':hashlib.file_digest(database.open('rb'),'sha256').hexdigest(),
              'query_cases_path':str(args.cases.resolve()),'query_cases_sha256':hashlib.sha256(args.cases.read_bytes()).hexdigest(),
              'trace_script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'python':sys.version,'apsw':apsw.apswversion(),'sqlite':apsw.sqlitelibversion(),
              'method':{'sqlite_page_cache_bytes':8*1024*1024,'sqlite_mmap_bytes':0,'sqlite_temp_store':'MEMORY','connection':'new read-only immutable connection per mode/case; count then page shares cache',
                        'remote_models':'identity-encoded byte ranges, aligned blocks; both unlimited-cache lower traffic and 8MiB remote LRU counted; 40ms per serial cache-miss request plus10Mbps payload time, no TLS/HTTP overhead/CPU/WASM/failed requests',
                        'interpretation':'Conditional serial demand-fetch transfer model, not observed browser or WAN latency. Parallel prefetch/coalescing, byte-range CDN support and an actual VFS may change requests. OS cache is uncontrolled but does not alter observed SQLite xRead demand.'},'cases':[]}
    compressed=database.with_name(database.name+'.gz')
    if compressed.exists():
        with gzip.open(compressed,'rb') as stream:
            restored_hash=hashlib.file_digest(stream,'sha256').hexdigest()
        if restored_hash!=report['database_sha256']:
            raise ValueError('Neighbor gzip does not reproduce selected database')
        report['verified_whole_gzip']={'bytes':compressed.stat().st_size,
            'sha256':hashlib.file_digest(compressed.open('rb'),'sha256').hexdigest(),
            'one_request_40ms_10mbps_seconds':0.04+8*compressed.stat().st_size/10000000}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    for case in cases:
        if not args.all_cases and not selected(case):
            continue
        row={key:case[key] for key in ('kind','term','edition','document','sort','page')}
        row['measurements']=[]
        outputs=[]
        for mode in (('optimized',) if args.optimized_only else ('baseline','optimized')):
            measurement,result=trace(database,case,mode,args.maximum_seconds)
            row['measurements'].append(measurement);outputs.append(result)
        if len(outputs)==2:
            assert outputs[0]==outputs[1],row
            row['baseline_optimized_exact_count_and_page_equal']=True
        report['cases'].append(row)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'kind':case['kind'],'term':case['term'],'edition':case['edition'],
                          'measurements':[{'mode':m['mode'],'count':m['count'],'xread_calls':m['xread_calls'],'unique_page_mib':round(m['unique_4k_page_bytes']/1048576,3),
                                           '64k_blocks':m['range_models'][0]['unique_blocks'],'64k_mib':round(m['range_models'][0]['cold_unique_bytes']/1048576,3)} for m in row['measurements']]}),flush=True)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--database',type=Path,required=True);parser.add_argument('--cases',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--optimized-only',action='store_true');parser.add_argument('--all-cases',action='store_true')
    parser.add_argument('--maximum-seconds',type=float,default=90)
    run(parser.parse_args())
