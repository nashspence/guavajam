// Read-only delivery of operator-verified, fixed B2 versions. Asset hashing runs in CI.
const SMALL = 10 * 1024 * 1024;
const LARGE = 20 * 1024 ** 3;
const MIME = {png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',webp:'image/webp',svg:'image/svg+xml',bin:'application/octet-stream',mp3:'audio/mpeg',mp4:'video/mp4',vtt:'text/vtt'};
const KEY = /^sha256\/([0-9a-f]{64})\.(png|jpg|jpeg|webp|svg|bin|mp3|mp4|vtt)$/;
const PIN = /^sha256\/\.pins\/[0-9a-f]{64}\.(png|jpg|jpeg|webp|svg|bin|mp3|mp4|vtt)\.json$/;
const encoder = new TextEncoder();
const hex = bytes => [...new Uint8Array(bytes)].map(b=>b.toString(16).padStart(2,'0')).join('');
async function hmac(key,text) {
  const material = await crypto.subtle.importKey('raw',key,{name:'HMAC',hash:'SHA-256'},false,['sign']);
  return crypto.subtle.sign('HMAC',material,encoder.encode(text));
}
export async function signedRead(env,key,version,method,range,instant=new Date()) {
  const endpoint=new URL(env.B2_S3_ENDPOINT);
  const match=/^s3\.([a-z0-9-]+)\.backblazeb2\.com$/.exec(endpoint.host);
  if(endpoint.protocol!=='https:'||!match||endpoint.pathname!=='/'||endpoint.search||endpoint.hash||endpoint.username||endpoint.password) throw Error('origin');
  if(!/^[A-Za-z0-9][A-Za-z0-9.-]{1,99}$/.test(env.B2_BUCKET_NAME)||!(KEY.test(key)||PIN.test(key))||(version===null?!PIN.test(key):!/^[A-Za-z0-9_-]{1,256}$/.test(version))) throw Error('identity');
  const stamp=instant.toISOString().replace(/[-:]|\.\d{3}/g,''),day=stamp.slice(0,8);
  const path='/'+encodeURIComponent(env.B2_BUCKET_NAME)+'/'+key;
  const query=version===null?'':'versionId='+encodeURIComponent(version);
  const digest=hex(await crypto.subtle.digest('SHA-256',new Uint8Array()));
  const canonicalHeaders='host:'+endpoint.host+'\nx-amz-content-sha256:'+digest+'\nx-amz-date:'+stamp+'\n';
  const names='host;x-amz-content-sha256;x-amz-date';
  const canonical=[method,path,query,canonicalHeaders,names,digest].join('\n');
  const scope=day+'/'+match[1]+'/s3/aws4_request';
  const message='AWS4-HMAC-SHA256\n'+stamp+'\n'+scope+'\n'+hex(await crypto.subtle.digest('SHA-256',encoder.encode(canonical)));
  let secret=encoder.encode('AWS4'+env.B2_APPLICATION_KEY);
  for(const part of [day,match[1],'s3','aws4_request']) secret=await hmac(secret,part);
  const signature=hex(await hmac(secret,message));
  const headers={'Accept-Encoding':'identity','X-Amz-Date':stamp,'X-Amz-Content-Sha256':digest,
    Authorization:'AWS4-HMAC-SHA256 Credential='+env.B2_APPLICATION_KEY_ID+'/'+scope+', SignedHeaders='+names+', Signature='+signature};
  if(range)headers.Range=range;
  return {url:endpoint.origin+path+(query?'?'+query:''),headers};
}
function failure(status,message='Asset unavailable',extra={}) {
  return new Response(message,{status,headers:{'Cache-Control':'no-store',...extra}});
}
function rangeFor(value,size) {
  const match=/^bytes=(?:(\d+)-(\d*)|-(\d+))$/.exec(value);
  if(!match)throw Error('range');
  const nums=match.slice(1).filter(v=>v!==undefined&&v!=='').map(Number);
  if(nums.some(n=>!Number.isSafeInteger(n)))throw Error('range');
  let start,end;
  if(match[3]!==undefined){if(Number(match[3])===0)throw Error('range');start=Math.max(0,size-Number(match[3]));end=size-1;}
  else{start=Number(match[1]);end=match[2]===''?size-1:Math.min(Number(match[2]),size-1);}
  if(start>=size||start>end)throw Error('range');
  return {start,end,length:end-start+1};
}
function bounded(body,length) {
  if(!body)throw Error('body');
  const reader=body.getReader();let received=0;
  return new ReadableStream({async pull(controller){
    try{const {done,value}=await reader.read();if(done){if(received!==length)throw Error('length');controller.close();return;}
      received+=value.byteLength;if(received>length)throw Error('length');controller.enqueue(value);
    }catch{await reader.cancel().catch(()=>{});controller.error(Error('Invalid asset stream'));}
  },cancel(reason){return reader.cancel(reason);}});
}
async function originRead(env,key,version,method='GET',range=null) {
  const signed=await signedRead(env,key,version,method,range);
  for(let attempt=0;attempt<3;attempt++) {
    const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),20000);
    try {
      const result=await fetch(signed.url,{method,headers:signed.headers,redirect:'manual',signal:abort.signal});
      if(![429,500,502,503,504].includes(result.status)||attempt===2)return result;
      await result.body?.cancel().catch(()=>{});
    } catch(error){if(attempt===2)throw error;}
    finally{clearTimeout(timer);}
    await new Promise(resolve=>setTimeout(resolve,100*(attempt+1)));
  }
}
async function mappingFor(env,key) {
  const upstream=await originRead(env,key.replace('sha256/','sha256/.pins/')+'.json',null);
  if(upstream.status===404)return null;
  const length=upstream.headers.get('Content-Length');
  if(upstream.status!==200||upstream.headers.get('Content-Type')?.split(';')[0].trim()!=='application/json'||!/^\d+$/.test(length||'')||Number(length)>8192||Number(length)===0)throw Error('mapping');
  return new Response(bounded(upstream.body,Number(length))).json();
}
export default {
  async fetch(request,env) {
    const url=new URL(request.url),key=url.pathname.slice(1),match=KEY.exec(key);
    if(url.origin!=='https://'+env.ASSET_HOST||url.href!==url.origin+url.pathname||url.search||url.hash||!match)return failure(404,'Not found');
    if(!['GET','HEAD'].includes(request.method))return failure(405,'Method not allowed',{Allow:'GET, HEAD'});
    let mapping;
    try{
      mapping=await mappingFor(env,key);
      if(!mapping)return failure(404,'Not found');
      if(Object.keys(mapping).sort().join(',')!=='byte_count,content_type,extension,retain_until,schema,sha256,version_id'||(mapping.retain_until!==null&&(!Number.isSafeInteger(mapping.retain_until)||mapping.retain_until<=0))||mapping.schema!=='press.asset.v1'||mapping.sha256!==match[1]||mapping.extension!==match[2]||
        !/^[A-Za-z0-9_-]{1,256}$/.test(mapping.version_id)||!Number.isSafeInteger(mapping.byte_count)||mapping.byte_count<0||(mapping.byte_count===0&&match[2]!=='bin')||
        mapping.byte_count>(['mp3','mp4'].includes(match[2])?LARGE:SMALL)||mapping.content_type!==MIME[match[2]])throw Error('mapping');
    }catch{return failure(503);}
    const wanted=request.headers.get('Range');let part;
    if(wanted){
      try{if(!['mp3','mp4'].includes(match[2])||request.method!=='GET')throw Error('range');part=rangeFor(wanted,mapping.byte_count);}
      catch{return failure(416,'Range not satisfiable',{'Content-Range':'bytes */'+mapping.byte_count,'Accept-Ranges':'bytes','Access-Control-Allow-Origin':'*'});}
    }
    let upstream;
    try{upstream=await originRead(env,key,mapping.version_id,request.method,wanted);}
    catch{return failure(502);}
    const expected=part?part.length:mapping.byte_count;
    if(upstream.status!==(part?206:200)||upstream.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase()!==mapping.content_type||
       upstream.headers.get('Content-Length')!==String(expected)||upstream.headers.get('x-amz-version-id')!==mapping.version_id||
       (upstream.headers.get('Content-Encoding')||'identity')!=='identity'||
       (part&&upstream.headers.get('Content-Range')!==`bytes ${part.start}-${part.end}/${mapping.byte_count}`)){
      await upstream.body?.cancel().catch(()=>{});return failure(502);
    }
    const headers=new Headers({'Content-Type':mapping.content_type,'Content-Length':String(expected),
      'Cache-Control':'public, max-age=31536000, immutable, no-transform','X-Content-Type-Options':'nosniff',
      'Access-Control-Allow-Origin':'*','Access-Control-Expose-Headers':'Content-Length, Content-Range, Accept-Ranges, X-Press-Asset-Version, X-Press-Retain-Until',
      'X-Press-Asset-Version':mapping.version_id,'X-Press-Retain-Until':String(mapping.retain_until||0)});
    if(['mp3','mp4'].includes(match[2]))headers.set('Accept-Ranges','bytes');
    if(part)headers.set('Content-Range',`bytes ${part.start}-${part.end}/${mapping.byte_count}`);
    if(match[2]==='bin')headers.set('Content-Disposition','attachment; filename="'+key.split('/')[1]+'"');
    try{return new Response(request.method==='HEAD'||expected===0?null:bounded(upstream.body,expected),{status:part?206:200,headers});}
    catch{return failure(502);}
  }
};
