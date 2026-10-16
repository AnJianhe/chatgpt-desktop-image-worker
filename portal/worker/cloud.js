import { bugs } from './bugs.js';
import { reportPage } from './report-page.js';
import { monitorPage } from './monitor-page.js';
import { page, workflow, catalog } from './assets.js';
const terminal = new Set(['done','error','not_generated']);
const states = new Set(['running','review',...terminal]);
const headers = {'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'};
const json = (data,status=200) => Response.json(data,{status,headers});
const id = () => crypto.randomUUID().replaceAll('-','');
const fail = (message,status=400) => {throw Object.assign(new Error(message),{status});};
async function body(request, limit=65536) {
 const raw=await request.text(); if(raw.length>limit)fail('请求过大',413);
 try{return JSON.parse(raw);}catch{fail('JSON 格式错误');}
}
export function imageType(bytes) {
 if(bytes[0]===137 && bytes[1]===80 && bytes[2]===78 && bytes[3]===71)return 'image/png';
 if(bytes[0]===255 && bytes[1]===216 && bytes[2]===255)return 'image/jpeg';
 if(String.fromCharCode(...bytes.slice(0,4))==='RIFF' && String.fromCharCode(...bytes.slice(8,12))==='WEBP')return 'image/webp';
 fail('仅支持 PNG、JPEG、WEBP 图片');
}
async function imageBody(request) {
 if(Number(request.headers.get('Content-Length'))>20*1024*1024)fail('图片超过 20 MB',413);
 const bytes=new Uint8Array(await request.arrayBuffer());
 if(bytes.length>20*1024*1024)fail('图片超过 20 MB',413);
 return {bytes,mime:imageType(bytes)};
}
export function validateTask(data) {
 if(typeof data.prompt!=='string'||!data.prompt.trim()||data.prompt.length>10000)fail('请输入不超过 10000 字的提示词');
 if(typeof data.request_id!=='string'||!data.request_id||data.request_id.length>128)fail('无效任务标识');
 const tool=catalog.tools.find(t=>t.id===(data.tool||'custom'));
 if(!tool)fail('请选择有效工具');
 const refs=data.upload_ids||[];
 if(!Array.isArray(refs)||refs.length>3||new Set(refs).size!==refs.length||refs.some(x=>typeof x!=='string'||! /^[a-f0-9]{32}$/.test(x)))fail('每个任务最多 3 张不同的参考图片');
 if(tool.requires_reference&&!refs.length)fail('请上传参考图片');
 return {prompt:data.prompt,tool:tool.id,refs,request_id:data.request_id};
}
function snapshot(row) {
 const data=JSON.parse(row.snapshot);
 const result={...data,id:row.id,taskId:row.id,prompt:row.prompt,tool:row.tool,referenceImages:JSON.parse(row.refs),created_at:row.created/1000,status:row.status};
 delete result.image_url; delete result.preview_url; delete result.result;
 if(row.status==='done'){result.image_url=`/jobs/${row.id}/image`;result.result=result.image_url;}
 else result.result=null;
 if(row.status==='review')result.preview_url=`/jobs/${row.id}/preview?v=${data.review_version}`;
 return result;
}
async function getObject(env,key) {
 const obj=await env.BUCKET.get(key);if(!obj)fail('图片尚未同步，请稍后重试',404);
 return new Response(obj.body,{headers:{...headers,'Content-Type':obj.httpMetadata?.contentType||'image/png'}});
}

export async function cloudFetch(request,env) {
 const url=new URL(request.url),path=url.pathname,method=request.method;
 const agent=request.headers.get('Authorization')===`Bearer ${env.CLOUD_WORKER_TOKEN}` && !!env.CLOUD_WORKER_TOKEN;
 const cookie=request.headers.get('Cookie')||'';
 let owner=cookie.match(/(?:^|;\s*)image_session=([a-f0-9]{32})(?:;|$)/)?.[1],fresh=false;
 if(!owner){owner=id();fresh=true;}
 const sql=(s,...args)=>env.DB.prepare(s).bind(...args);
 const task=async taskId=>{const row=await sql('SELECT * FROM cloud_tasks WHERE id=?',taskId).first();if(!row||(!agent&&row.owner!==owner))fail('任务不存在',404);return row;};
 let response;
 try{
 if(!agent&&!['GET','HEAD'].includes(method)&&request.headers.get('Origin')&&request.headers.get('Origin')!==url.origin)fail('跨站请求已拒绝',403);
 if(path.startsWith('/worker/')&&!agent)fail('Unauthorized',401);
 const bugResponse=await bugs(request,env,{sql,owner,agent,body,json,fail});
 if(bugResponse)response=bugResponse;
 else if(path==='/report-bug'&&method==='GET')response=new Response(reportPage,{headers:{...headers,'Content-Type':'text/html; charset=utf-8'}});
 else if(path==='/'&&method==='GET')response=new Response(page,{headers:{...headers,'Content-Type':'text/html; charset=utf-8'}});
 else if(path==='/static/workflow.js')response=new Response(workflow,{headers:{...headers,'Content-Type':'text/javascript; charset=utf-8'}});
 else if(path==='/monitor')response=new Response(monitorPage,{headers:{...headers,'Content-Type':'text/html; charset=utf-8'}});
 else if(path==='/api/monitor'||path==='/api/latest'){
  const row=await sql('SELECT * FROM cloud_worker WHERE id=1').first();const data=row?JSON.parse(row.payload):{};
  let kuma=data.kuma||null,kuma_updated=null;
  if(path==='/api/monitor'){const obj=await env.BUCKET.get('monitor/kuma.json');if(obj){const saved=await new Response(obj.body).json();kuma=saved.kuma;kuma_updated=saved.updated;}}
  response=json({kuma_updated,kuma_stale:!kuma_updated||Date.now()-kuma_updated>120000,ready:!!row&&Date.now()-row.updated<30000&&data.local_health?.worker_alive===true,updated:row?.updated||null,kuma,transport:'outbound',version:'cloud-1.0.4',worker_version:typeof data.local_health?.automation_version==='string'?data.local_health.automation_version:null});
 }else if(path==='/api/tunnel-heartbeat'){
  const d=method==='POST'?await body(request):{};response=json({nonce:d.nonce,server_id:'cloud-v1',status:'ok'});
 }else if(path==='/uploads'&&method==='POST'){
  if(Number(request.headers.get('Content-Length'))>21*1024*1024)fail('图片超过 20 MB',413);
  const form=await request.formData(),file=form.get('image')||form.get('file');if(!file||typeof file.arrayBuffer!=='function')fail('请选择图片');
  if(file.size>20*1024*1024)fail('图片超过 20 MB',413);
  const bytes=new Uint8Array(await file.arrayBuffer()),mime=imageType(bytes),uploadId=id();
  await env.BUCKET.put(`uploads/${uploadId}`,bytes,{httpMetadata:{contentType:mime}});
  await sql('INSERT INTO cloud_uploads (id,owner,mime,created) VALUES (?,?,?,?)',uploadId,owner,mime,Date.now()).run();response=json({upload_id:uploadId},201);
 }else if(path==='/jobs'&&method==='POST'){
  const d=validateTask(await body(request));
  for(const ref of d.refs){if(!await sql('SELECT id FROM cloud_uploads WHERE id=? AND owner=?',ref,owner).first())fail('参考图片不存在，请重新上传');}
  const existing=await sql('SELECT * FROM cloud_tasks WHERE owner=? AND request_id=?',owner,d.request_id).first();
  if(existing){if(existing.prompt!==d.prompt||existing.tool!==d.tool||existing.refs!==JSON.stringify(d.refs))fail('任务标识重复',409);response=json({id:existing.id,status:existing.status});}
  else{const taskId=id(),now=Date.now();await sql('INSERT OR IGNORE INTO cloud_tasks (id,owner,request_id,created,updated,status,prompt,tool,refs,snapshot) VALUES (?,?,?,?,?,?,?,?,?,?)',taskId,owner,d.request_id,now,now,'queued',d.prompt,d.tool,JSON.stringify(d.refs),JSON.stringify({stage:'排队中',error:null})).run();const saved=await sql('SELECT * FROM cloud_tasks WHERE owner=? AND request_id=?',owner,d.request_id).first();if(saved.prompt!==d.prompt||saved.tool!==d.tool||saved.refs!==JSON.stringify(d.refs))fail('任务标识重复',409);response=json({id:saved.id,status:saved.status},202);}
 }else if(path==='/history'){
  const limit=Math.min(100,Math.max(1,Number(url.searchParams.get('limit'))||50)),offset=Math.max(0,Number(url.searchParams.get('offset'))||0);
  const status=url.searchParams.get('status'),filter=status?' AND status=?':'',args=status?[owner,status]:[owner];
  const rows=await sql(`SELECT * FROM cloud_tasks WHERE owner=?${filter} ORDER BY created DESC LIMIT ? OFFSET ?`,...args,limit,offset).all();
  const count=await sql(`SELECT count(*) AS n FROM cloud_tasks WHERE owner=?${filter}`,...args).first();response=json({items:rows.results.map(snapshot),total:count.n,limit,offset});
 }else if(path==='/worker/kuma'&&method==='POST'){
  const d=await body(request,524288);if(!Array.isArray(d.monitors)||d.monitors.length>16)fail('监控数据格式错误');
  await env.BUCKET.put('monitor/kuma.json',JSON.stringify({updated:Date.now(),kuma:d}),{httpMetadata:{contentType:'application/json'}});response=json({ok:true});
 }else if(path==='/worker/heartbeat'&&method==='POST'){
  const d=await body(request);await sql('INSERT INTO cloud_worker (id,updated,payload) VALUES (1,?,?) ON CONFLICT(id) DO UPDATE SET updated=excluded.updated,payload=excluded.payload',Date.now(),JSON.stringify(d)).run();response=json({ok:true});
 }else if(path==='/worker/claim'&&method==='POST'){
  let row=await sql("SELECT * FROM cloud_tasks WHERE status IN ('running','review') ORDER BY created LIMIT 1").first();
  if(!row)row=await sql("UPDATE cloud_tasks SET status='running',worker='primary',updated=? WHERE id=(SELECT id FROM cloud_tasks WHERE status='queued' ORDER BY created LIMIT 1) AND NOT EXISTS (SELECT 1 FROM cloud_tasks WHERE status IN ('running','review')) RETURNING *",Date.now()).first();
  response=json({task:row?{...snapshot(row),upload_ids:JSON.parse(row.refs),local_id:row.local_id}:null});
 }else if(path.startsWith('/worker/uploads/')&&method==='GET'){
  response=await getObject(env,'uploads/'+path.split('/').pop());
 }else if(/^\/worker\/tasks\/[a-f0-9]{32}(?:\/.*)?$/.test(path)){
  const parts=path.split('/'),row=await task(parts[3]),op=parts[4];
  if(op==='snapshot'&&method==='POST'){
   const d=await body(request);if(!states.has(d.status))fail('无效状态');
   if(terminal.has(row.status)){response=json({ok:true,status:row.status});}
   else{
    if(d.status==='done'&&!await env.BUCKET.head(`results/${row.id}`))fail('结果图片尚未上传',409);
    if(d.status==='review'&&!await env.BUCKET.head(`previews/${row.id}/${Number(d.review_version)}`))fail('确认截图尚未上传',409);
    const safe={stage:String(d.stage||''),error:d.error?String(d.error):null,review_version:Number(d.review_version)||0,image_filename:String(d.image_filename||'image.png')};
    await sql('UPDATE cloud_tasks SET status=?,updated=?,snapshot=?,local_id=COALESCE(?,local_id) WHERE id=?',d.status,Date.now(),JSON.stringify(safe),d.local_id||null,row.id).run();response=json({ok:true});
   }
  }else if(op==='log'&&method==='PUT'){
   await env.BUCKET.put('logs/'+row.id,request.body,{httpMetadata:{contentType:'application/x-ndjson; charset=utf-8'}});response=json({ok:true});
  }else if((op==='preview'||op==='result')&&method==='PUT'){
   const {bytes,mime}=await imageBody(request);const key=op==='result'?`results/${row.id}`:`previews/${row.id}/${Number(url.searchParams.get('v'))||0}`;
   await env.BUCKET.put(key,bytes,{httpMetadata:{contentType:mime}});response=json({ok:true});
  }else if(op==='decision'&&method==='GET')response=json({decision:row.decision?JSON.parse(row.decision):null});
  else if(op==='decision'&&method==='POST'){
   const d=await body(request);await sql('UPDATE cloud_tasks SET decision=NULL WHERE id=? AND decision=?',row.id,JSON.stringify(d)).run();response=json({ok:true});
  }else fail('Not found',404);
 }else if(/^\/jobs\/[a-f0-9]{32}(?:\/.*)?$/.test(path)){
  const parts=path.split('/'),row=await task(parts[2]),op=parts[3];
  if(!op&&method==='GET')response=json(snapshot(row));
  else if(op==='log'&&method==='GET')response=await getObject(env,'logs/'+row.id);
  else if(op==='preview'&&method==='GET')response=await getObject(env,`previews/${row.id}/${JSON.parse(row.snapshot).review_version}`);
  else if(op==='image'&&method==='GET'){
   if(row.status!=='done')fail('图片尚未生成',409);response=await getObject(env,`results/${row.id}`);
   const name=JSON.parse(row.snapshot).image_filename||'image.png';response.headers.set('Content-Disposition',`attachment; filename*=UTF-8''${encodeURIComponent(name.replace(/[\r\n]/g,''))}`);
  }else if(op==='review'&&method==='POST'){
   const d=await body(request);if(!['retry','wait','end'].includes(d.action)||!Number.isInteger(d.version)||(d.action==='end'&&d.confirmed!==true))fail('请确认操作');
   const current=JSON.parse(row.snapshot);if(row.status!=='review'||row.decision||d.version!==current.review_version)fail('任务状态已更新，请刷新查看',409);
   const decision=JSON.stringify({action:d.action,version:d.version,confirmed:d.confirmed===true});
   const change=await sql("UPDATE cloud_tasks SET decision=? WHERE id=? AND status='review' AND decision IS NULL AND snapshot=?",decision,row.id,row.snapshot).run();
   if(change.meta?.changes===0)fail('任务状态已更新',409);response=json({id:row.id,status:'running'},202);
  }else fail('Not found',404);
 }else if(path==='/api/update'&&method==='POST')response=json({ok:true,transport:'outbound'});
 else fail('Not found',404);
 }catch(error){if(!error.status)console.error('Cloud request failed',path,error.message);response=json({error:error.status?error.message:'云端服务暂时不可用'},error.status||503);}
 if(fresh){response.headers.append('Set-Cookie',`image_session=${owner}; Path=/; Max-Age=31536000; HttpOnly; Secure; SameSite=Lax`);}
 return response;
}
