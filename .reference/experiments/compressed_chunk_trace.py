#!/usr/bin/env python3
"""Model actual gzip bytes of physical SQLite chunks demanded by real xReads.

The SQLite engine still sees one complete logical database. This is a transfer
model only; it is not a browser VFS implementation or a ranking approximation.
"""
import argparse
from collections import Counter, OrderedDict
import gzip
import hashlib
import json
from pathlib import Path
import sys
import zlib

import apsw
import range_trace


class ChunkSizes:
    def __init__(self, database):
        self.database=database
        self.compressed={}
        self.raw={}

    def size(self, block_size, block):
        key=(block_size,block)
        if key not in self.compressed:
            with self.database.open('rb') as stream:
                stream.seek(block_size*block)
                raw=stream.read(block_size)
            self.raw[key]=len(raw)
            self.compressed[key]=len(gzip.compress(raw,compresslevel=9,mtime=0))
        return self.compressed[key]


class Model(range_trace.BlockModel):
    def __init__(self,size,database_bytes,cache_bytes,chunks):
        super().__init__(size,database_bytes,cache_bytes)
        self.chunks=chunks
        self.misses=Counter()
        self.miss_order=[]

    def read(self,offset,amount):
        end=min(offset+amount,self.database_bytes)
        for block in range(offset//self.size,(end+self.size-1)//self.size):
            size=min(self.size,self.database_bytes-block*self.size)
            if block not in self.seen:self.seen.add(block);self.unique_bytes+=size
            if block in self.lru:self.lru.move_to_end(block)
            else:
                self.requests+=1;self.transferred+=size;self.misses[block]+=1;self.miss_order.append(block);self.lru[block]=True
                if len(self.lru)>self.capacity:self.lru.popitem(last=False)

    def output(self):
        result=super().output()
        unique=sum(self.chunks.size(self.size,block) for block in self.seen)
        lru=sum(self.chunks.size(self.size,block)*count for block,count in self.misses.items())
        result.update(unique_gzip_bytes=unique,lru_gzip_transfer_bytes=lru,
            gzip_unlimited_cache_serial_40ms_10mbps_seconds=0.04*len(self.seen)+8*unique/10000000,
            gzip_lru_serial_40ms_10mbps_seconds=0.04*self.requests+8*lru/10000000)
        result['with_separate_compressed_object_cache']=[]
        for budget in (8*1024*1024,32*1024*1024):
            compressed_lru=OrderedDict();resident=0;requests=0;transferred=0
            for block in self.miss_order:
                if block in compressed_lru:
                    compressed_lru.move_to_end(block)
                    continue
                amount=self.chunks.size(self.size,block)
                requests+=1;transferred+=amount
                while compressed_lru and resident+amount>budget:
                    _,evicted=compressed_lru.popitem(last=False);resident-=evicted
                if amount<=budget:
                    compressed_lru[block]=amount;resident+=amount
            result['with_separate_compressed_object_cache'].append({
                'compressed_cache_budget_bytes':budget,'compressed_cache_final_bytes':resident,
                'network_requests':requests,'gzip_network_bytes':transferred,
                'conditional_serial_40ms_10mbps_seconds':0.04*requests+8*transferred/10000000,
                'decoded_cache_misses':self.requests,'decompressed_bytes_including_repeats':self.transferred})
        return result


class Recorder(range_trace.Recorder):
    def __init__(self,size,chunks):
        super().__init__(size)
        self.models=[Model(block,size,cache,chunks) for cache in (8*1024*1024,32*1024*1024)
                     for block in (256*1024,1024*1024,4*1024*1024)]


def run(args):
    database=args.database.resolve(strict=True)
    with database.open('rb') as stream:database_hash=hashlib.file_digest(stream,'sha256').hexdigest()
    report={'database':str(database),'database_bytes':database.stat().st_size,'database_sha256':database_hash,
            'query_cases_path':str(args.cases.resolve()),'query_cases_sha256':hashlib.sha256(args.cases.read_bytes()).hexdigest(),
            'chunk_script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'trace_script_sha256':hashlib.sha256(Path(range_trace.__file__).read_bytes()).hexdigest(),
            'python':sys.version,'apsw':apsw.apswversion(),'sqlite':apsw.sqlitelibversion(),'zlib':zlib.ZLIB_VERSION,
            'method':'Observed SQLite 3.54.1 xRead requests; fresh 8 MiB SQLite cache, mmap 0, immutable; count then page. Independently gzip9-compressed 256 KiB / 1 MiB / 4 MiB physical chunks; 8 / 32 MiB decoded chunk LRU. The first model charges network for every decoded miss; a second model adds an independently bounded 8 / 32 MiB compressed-object LRU. Conditional serial 40 ms / request + 10 Mbps compressed payload excludes decompression, integrity, manifest, HTTP headers, runtime, and SQLite CPU; not browser qualification.',
            'cases':[]}
    compressed=database.with_name(database.name+'.gz')
    if compressed.exists():
        with gzip.open(compressed,'rb') as stream:
            assert hashlib.file_digest(stream,'sha256').hexdigest()==database_hash
        report['verified_whole_gzip_bytes']=compressed.stat().st_size
        report['whole_gzip_40ms_10mbps_seconds']=0.04+8*compressed.stat().st_size/10000000
    chunks=ChunkSizes(database)
    cases=json.loads(args.cases.read_text())
    args.output.parent.mkdir(parents=True,exist_ok=True)
    for case in cases:
        if not (case['kind']=='passages' and case['term'] in {'contract','transaction','absentmatchingwordxyz'}
                and case['edition']=='all' and case['sort']=='relevance' and case['page']==1):continue
        baseline,oracle=range_trace.trace(database,case,'baseline',90)
        measurement,result=range_trace.trace(database,case,'optimized',90,Recorder(database.stat().st_size,chunks))
        assert result==oracle
        row={'kind':case['kind'],'term':case['term'],'edition':case['edition'],
             'baseline_optimized_exact_count_and_page_equal':True,**measurement}
        report['cases'].append(row)
        report['compressed_chunk_sizes']={str(size):{str(block):compressed for (s,block),compressed in sorted(chunks.compressed.items()) if s==size}
                                          for size in (256*1024,1024*1024,4*1024*1024)}
        args.output.write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps({'term':case['term'],'count':row['count'],'models':[{'chunk_mib':x['block_bytes']/1048576,'cache_mib':x['remote_cache_bytes']/1048576,
            'requests':x['lru_requests'],'gzip_mib':round(x['lru_gzip_transfer_bytes']/1048576,3),'seconds':round(x['gzip_lru_serial_40ms_10mbps_seconds'],3)} for x in row['range_models']]}),flush=True)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--database',type=Path,required=True);parser.add_argument('--cases',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args())
