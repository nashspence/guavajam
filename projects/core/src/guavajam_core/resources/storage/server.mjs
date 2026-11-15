// Local HTTP transport for the SAME gateway handler deployed to Workers.
import {createServer} from 'node:http';
import {openSync,closeSync,fstatSync,readSync} from 'node:fs';
import {Readable} from 'node:stream';
import {pipeline} from 'node:stream/promises';
import worker, {signedRead} from './worker.js';

export function configuration(values=process.env) {
  const env={...values};
  for(const name of ['S3_ACCESS_KEY_ID','S3_SECRET_ACCESS_KEY','S3_SESSION_TOKEN']){
    if(env[name+'_FILE']){
      if(env[name])throw Error('configure one credential source');
      const fd=openSync(env[name+'_FILE'],'r');
      try{
        const info=fstatSync(fd);
        if(!info.isFile()||info.size>65536)throw Error('credential file exceeds limit');
        const raw=Buffer.alloc(65537);let length=0,received;
        while(length<raw.length && (received=readSync(fd,raw,length,raw.length-length,null))>0)length+=received;
        if(length>65536)throw Error('credential file exceeds limit');
        env[name]=raw.subarray(0,length).toString('utf8').trim();
      }finally{closeSync(fd);}
    }
  }
  if(!/^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/.test(env.ASSET_HOST||''))throw Error('configure canonical asset host');
  return env;
}

export function server(env) {
  return createServer(async(req,res)=>{
    try{
      if(req.url==='/healthz' && ['GET','HEAD'].includes(req.method)){
        res.writeHead(200,{'Content-Type':'text/plain','Cache-Control':'no-store'});
        res.end(req.method==='HEAD'?undefined:'ok\n');return;
      }
      if(!req.url.startsWith('/')||req.url.startsWith('//')){
        res.writeHead(400);res.end();return;
      }
      // The operator's canonical origin is independent of client Host/Forwarded.
      const request=new Request('https://'+env.ASSET_HOST+req.url,{method:req.method,
        headers:req.headers.range?{Range:req.headers.range}:{}});
      req.resume();
      const result=await worker.fetch(request,env);
      res.writeHead(result.status,Object.fromEntries(result.headers));
      if(!result.body){res.end();return;}
      await pipeline(Readable.fromWeb(result.body),res);
    }catch{
      if(!res.headersSent){res.writeHead(502,{'Cache-Control':'no-store'});res.end('Asset unavailable');}
      else res.destroy();
    }
  });
}

if(import.meta.url===new URL(process.argv[1],'file:').href){
  try{
    const env=configuration();
    await signedRead(env,'sha256/.released/'+'a'.repeat(64)+'.bin.json',null,'GET',null);
    const port=Number(process.env.PORT||8080);
    if(!Number.isInteger(port)||port<1||port>65535)throw Error('port');
    const listener=server(env);listener.requestTimeout=30000;listener.headersTimeout=10000;
    listener.listen(port,'0.0.0.0');
    for(const sig of ['SIGTERM','SIGINT'])process.on(sig,()=>listener.close(()=>process.exit(0)));
  }catch{process.stderr.write('Asset gateway configuration failed; details omitted\n');process.exitCode=1;}
}
