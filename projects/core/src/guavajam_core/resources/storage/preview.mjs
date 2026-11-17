// Authenticated private preview transport. The private HTML and asset responses are
// same-origin, noncacheable, exact-inventory, and use the production byte handler.
// Credentials live only in this process; source-controlled code never executes.
import {createServer} from 'node:http';
import {readFileSync,lstatSync,realpathSync} from 'node:fs';
import {resolve,join} from 'node:path';
import {Readable} from 'node:stream';
import {pipeline} from 'node:stream/promises';
import {timingSafeEqual} from 'node:crypto';
import {previewFetch} from './worker.js';
import {configuration} from './server.mjs';

const types={'.html':'text/html; charset=utf-8','.json':'application/json','.gfm':'text/plain; charset=utf-8','.xml':'application/xml'};

// Preview-only *response* adaptation. Signed canonical files on disk are never
// rewritten: a later production build can be compared byte-for-byte. Only
// actual HTML attributes referencing the exact, privately qualified inventory
// are routed to the session-bound asset proxy; literal code/text stays intact.
export function privateHtml(raw,manifest) {
  const addresses=new Map(Object.keys(manifest.assets).map(key=>[manifest.origin+'/'+key,'/_assets/'+key]));
  let html=raw.toString('utf8');
  // Match only real opening tags. Replacing text globally can silently change
  // literal `src="..."` examples in code blocks and the author's prose.
  html=html.replace(/<[A-Za-z][^>]*>/g,tag=>tag.replace(/(\b(?:src|href|poster)=["'])([^"']+)(["'])/g,
    (full,start,url,end)=>addresses.has(url)?start+addresses.get(url)+end:full));
  // The published baseline may still use its public origin, while staged assets
  // use this same-origin private gateway. Permit BOTH, without altering files.
  html=html.replace(/(<meta http-equiv="Content-Security-Policy" content=")([^"]+)(")/, (full,start,csp,end)=>
    start+csp.replace(/((?:img-src|media-src)\s+)([^;]+)/g,(directive,kind,values)=>
      kind+(values.includes('&#x27;self&#x27;')?values:values.trimEnd()+' &#x27;self&#x27;'))+end);
  return Buffer.from(html);
}
function deny(res,status=404){res.writeHead(status,{'Cache-Control':'no-store','Content-Type':'text/plain','X-Content-Type-Options':'nosniff'});res.end('Not found');}
function equal(a,b){const x=Buffer.from(a),y=Buffer.from(b);return x.length===y.length&&timingSafeEqual(x,y);}

export function previewServer(env,root,manifest,token,webOrigin=null) {
  if(webOrigin){
    const url=new URL(webOrigin);
    const loopback=url.protocol==='http:'&&url.hostname==='127.0.0.1';
    if((url.protocol!=='https:'&&!loopback)||url.origin!==webOrigin||url.username||url.password)throw Error('HTTPS or explicit localhost preview origin required');
  }
  if(!/^[a-f0-9]{64}$/.test(token))throw Error('preview token');
  if(manifest.schema!=='guavajam.preview.v1'||!manifest.files||!manifest.assets)throw Error('preview manifest');
  root=realpathSync(root);
  return createServer(async(req,res)=>{
    try {
      // Loopback binding alone is not enough: reject rebinding Host and cross-site
      // browser fetches before reading private source or storage.
      const host=webOrigin?new URL(webOrigin).host:'127.0.0.1:'+req.socket.localPort;
      const origin=webOrigin||'http://'+host;
      if(req.headers.host!==host||(!webOrigin&&!['127.0.0.1','::ffff:127.0.0.1'].includes(req.socket.remoteAddress)))return deny(res);
      if(!['GET','HEAD'].includes(req.method))return deny(res,405);
      if(req.headers['sec-fetch-site']==='cross-site')return deny(res);
      if(req.headers.origin&&req.headers.origin!==origin)return deny(res);
      if(!req.url.startsWith('/')||req.url.startsWith('//')||req.url.includes('\\'))return deny(res);
      const url=new URL(req.url,origin);
      // Token is supplied once in an explicit local URL. Never send it upstream.
      if(url.pathname==='/_session'&&req.method==='GET'&&url.searchParams.size===1&&equal(url.searchParams.get('token')||'',token)){
        res.writeHead(303,{'Location':'/','Cache-Control':'no-store','Referrer-Policy':'no-referrer',
          'Set-Cookie':'guavajam_preview='+token+'; Path=/; HttpOnly; SameSite=Strict'+(webOrigin?.startsWith('https://')?'; Secure':'')});res.end();return;
      }
      const cookies=(req.headers.cookie||'').split(';').map(x=>x.trim());
      if(!cookies.some(x=>x.startsWith('guavajam_preview=')&&equal(x.slice(17),token)))return deny(res);
      if(url.search||url.hash||decodeURI(url.pathname)!==url.pathname)return deny(res);
      req.resume();
      if(url.pathname.startsWith('/_assets/')){
        const key=url.pathname.slice('/_assets/'.length);
        if(!Object.hasOwn(manifest.assets,key))return deny(res);
        const request=new Request('https://'+env.ASSET_HOST+'/'+key,{method:req.method,headers:req.headers.range?{Range:req.headers.range}:{}});
        const result=await previewFetch(request,env,manifest.assets[key]);
        res.writeHead(result.status,{...Object.fromEntries(result.headers),'cache-control':'private, no-store','referrer-policy':'no-referrer','cross-origin-resource-policy':'same-origin'});
        if(!result.body){res.end();return;}
        await pipeline(Readable.fromWeb(result.body),res);return;
      }
      const name=url.pathname.slice(1)+(url.pathname.endsWith('/')?'index.html':'');
      if(!Object.hasOwn(manifest.files,name))return deny(res);
      const target=resolve(root,name);
      if(!target.startsWith(root+'/')||realpathSync(target)!==target||!lstatSync(target).isFile())return deny(res);
      const content=readFileSync(target);
      const {createHash}=await import('node:crypto');
      if(createHash('sha256').update(content).digest('hex')!==manifest.files[name])return deny(res,503);
      const type=Object.entries(types).find(([suffix])=>name.endsWith(suffix))?.[1]||'application/octet-stream';
      const presented=name.endsWith('.html')?privateHtml(content,manifest):content;
      res.writeHead(200,{'Content-Type':type,'Content-Length':String(presented.length),'Cache-Control':'private, no-store',
        'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','Cross-Origin-Resource-Policy':'same-origin',
        'X-Robots-Tag':'noindex, nofollow, noarchive','Content-Security-Policy':"frame-ancestors 'none'"});
      res.end(req.method==='HEAD'?undefined:presented);
    }catch{if(!res.headersSent)deny(res,503);else res.destroy();}
  });
}

if(process.argv[1]&&import.meta.url===new URL(process.argv[1],'file:').href){
  try{
    const root=resolve(process.argv[2]);
    // Created by trusted rendering, not read from the author repository.
    const manifest=JSON.parse(readFileSync(join(root,'PREVIEW.json'),'utf8'));
    const values=JSON.parse(readFileSync(0,'utf8'));
    const env=Object.keys(manifest.assets).length?configuration(values.storage):{};
    const server=previewServer(env,root,manifest,values.token,values.webOrigin||null);
    const port=Number(process.env.GUAVAJAM_PREVIEW_PORT||0);
    if(!Number.isInteger(port)||port<0||port>65535)throw Error('port');
    server.requestTimeout=30000;server.headersTimeout=10000;
    server.on('error',()=>{process.stderr.write('Private preview listener unavailable\n');process.exitCode=1;});
    server.listen(port,values.webOrigin?'0.0.0.0':'127.0.0.1',()=>{
      if(values.webOrigin)console.log('Private preview listening; session access is operator-provisioned');
      else console.log('Private preview: http://127.0.0.1:'+server.address().port+'/_session?token='+values.token);
    });
    for(const sig of ['SIGTERM','SIGINT'])process.on(sig,()=>server.close(()=>process.exit(0)));
  }catch{process.stderr.write('Private preview failed; details omitted\n');process.exitCode=1;}
}
