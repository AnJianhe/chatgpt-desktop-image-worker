import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync,readdirSync} from 'node:fs';
import {cloudFetch,validateTask} from '../worker/cloud.js';
import {monitorPage} from '../worker/monitor-page.js';
const png=Uint8Array.from([137,80,78,71,13,10,26,10]);
test('monitor: Simplified Chinese UI and valid inline script',()=>{
 assert.match(monitorPage,/lang="zh-CN"/);
 assert.match(monitorPage,/响应时间/);
 assert.match(monitorPage,/最近一年/);
 new Function(monitorPage.match(/<script>([\s\S]*?)<\/script>/)[1]);
});
test('bugs: user reports without classification, VM receipt, maintainer classification and task log isolation',async()=>{
 const f=fixture();try{
  const data={request_id:'a'.repeat(32),description:'已有三张图片却提示请选择图片'};
  const created=await f.call('/bugs',data);assert.equal(created.status,201);
  const report=created.value.report;assert.equal(report.category,'unknown');assert.equal(report.delivered,null);
  assert.equal((await f.call('/bugs',data)).value.report.id,report.id);
  assert.equal((await f.call('/worker/bugs')).status,401);
  assert.equal((await f.call('/admin/bugs')).status,401);
  assert.equal((await f.call('/worker/bugs',undefined,true)).value.items[0].id,report.id);
  await f.call('/worker/bugs/'+report.id+'/receipt',{},true);
  assert.ok((await f.call('/bugs/'+report.id)).value.report.delivered);
  const admin=async(path,data)=>{const r=await cloudFetch(new Request('https://example.test'+path,{method:data?'POST':'GET',headers:{Authorization:'Bearer admin-test','Content-Type':'application/json'},body:data?JSON.stringify(data):undefined}),f.env);return {status:r.status,value:await r.json()};};
  assert.equal((await admin('/admin/bugs/'+report.id,{status:'fixed',category:'sites',reply:'已修复上传字段'})).status,200);
  const receipt=(await f.call('/bugs/'+report.id)).value.report;assert.equal(receipt.status,'fixed');assert.equal(receipt.category,'sites');assert.equal(receipt.reply,'已修复上传字段');
  const stranger=await cloudFetch(new Request('https://example.test/bugs/'+report.id),f.env);assert.equal(stranger.status,404);
  assert.equal((await f.call('/worker/bugs',undefined,true)).value.items.length,0);
  const job=(await f.call('/jobs',{prompt:'日志测试',tool:'custom',request_id:'log-test'})).value;
  const log=new TextEncoder().encode('{"event":"full-log"}\n');
  assert.equal((await f.call('/worker/tasks/'+job.id+'/log',log,true,'PUT')).status,200);
  assert.deepEqual((await f.call('/jobs/'+job.id+'/log')).value,{event:'full-log'});
 }finally{f.db.close()}
});
test('monitor: authenticated independent sync survives legacy heartbeat and flags stale records',async()=>{
 const f=fixture();try{
  assert.equal((await f.call('/worker/kuma',{monitors:[]})).status,401);
  const payload={monitors:[{id:1,name:'画图服务',status:1,stats:{'24h':{uptime:99.9}}}]};
  assert.equal((await f.call('/worker/kuma',payload,true)).status,200);
  await f.call('/worker/heartbeat',{local_health:{worker_alive:true},kuma:{monitors:[]}},true);
  const d=(await f.call('/api/monitor')).value;
  assert.equal(d.kuma.monitors[0].stats['24h'].uptime,99.9);
  assert.equal(d.kuma_stale,false);assert.equal(d.ready,true);
  f.objects.get('monitor/kuma.json').body=JSON.stringify({updated:Date.now()-121000,kuma:payload});
  assert.equal((await f.call('/api/monitor')).value.kuma_stale,true);
  assert.equal((await f.call('/worker/kuma',{monitors:Array(17).fill({})},true)).status,400);
 }finally{f.db.close()}
});
function fixture(){
 const db=new DatabaseSync(':memory:');
 for(const file of readdirSync('drizzle').filter(x=>x.endsWith('.sql')).sort())db.exec(readFileSync('drizzle/'+file,'utf8'));
 const objects=new Map();
 const env={CLOUD_WORKER_TOKEN:'unit-test',BUG_ADMIN_TOKEN:'admin-test',DB:{prepare(s){return {bind(...args){const q=db.prepare(s);return {async first(){return q.get(...args)||null},async all(){return {results:q.all(...args)}},async run(){return {meta:{changes:q.run(...args).changes}}}}}}}},BUCKET:{async put(key,bytes,options){if(bytes instanceof ReadableStream)bytes=new Uint8Array(await new Response(bytes).arrayBuffer());objects.set(key,{body:bytes,httpMetadata:options.httpMetadata})},async get(key){return objects.get(key)},async head(key){return objects.get(key)}}};
 let cookie='';
 async function call(path,data,worker=false,method){
  const headers={};if(worker)headers.Authorization='Bearer unit-test';else if(cookie)headers.Cookie=cookie;
  let body;if(data instanceof FormData)body=data;else if(data instanceof Uint8Array)body=data;else if(data!==undefined){body=JSON.stringify(data);headers['Content-Type']='application/json';}
  const r=await cloudFetch(new Request('https://example.test'+path,{method:method||(body?'POST':'GET'),headers,body}),env);
  if(!worker&&!cookie)cookie=r.headers.get('set-cookie').split(';')[0];
  const value=r.headers.get('content-type')?.includes('json')?await r.json():await r.text();return {status:r.status,value};
 }
 return {call,env,db,objects};
}
test('cloud: 0–3 references, required images, custom prompt and ownership',async()=>{
 const f=fixture();try{
  const prompt='  人物摄影\n不附加布料词  ';
  let r=await f.call('/jobs',{prompt,request_id:'a',tool:'custom',upload_ids:[]});assert.equal(r.status,202);
  assert.equal((await f.call('/jobs/'+r.value.id)).value.prompt,prompt);
  assert.equal((await f.call('/jobs',{prompt,request_id:'b',tool:'extract'})).status,400);
  const refs=[];
  for(let i=0;i<3;i++){const form=new FormData();form.set('image',new Blob([png],{type:'image/png'}),'r.png');form.set('upload_id',String(i+1).padStart(32,'0'));const uploaded=await f.call('/uploads',form);assert.equal(uploaded.status,201);refs.push(uploaded.value.upload_id);const j=await f.call('/jobs',{prompt,request_id:'ref'+i,tool:'custom',upload_ids:refs});assert.equal(j.status,202);assert.equal((await f.call('/jobs/'+j.value.id)).value.referenceImages.length,i+1);}
  const oldForm=new FormData();oldForm.set('file',new Blob([png],{type:'image/png'}),'legacy.png');assert.equal((await f.call('/uploads',oldForm)).status,201);
  assert.throws(()=>validateTask({prompt,request_id:'too-many',upload_ids:[...refs,'a'.repeat(32)]}),/最多/);
  const stranger=await cloudFetch(new Request('https://example.test/jobs/'+r.value.id),f.env);assert.equal(stranger.status,404);
  assert.equal((await f.call('/worker/claim',{})).status,401);
  const duplicate=await f.call('/jobs',{prompt,request_id:'a',tool:'custom'});assert.equal(duplicate.value.id,r.value.id);
  assert.equal((await f.call('/jobs',{prompt:'different',request_id:'a',tool:'custom'})).status,409);
 }finally{f.db.close()}
});
test('cloud: serial claims, background auto-download, review end and next task',async()=>{
 const f=fixture();try{
  const a=(await f.call('/jobs',{prompt:'A',request_id:'a',tool:'custom'})).value;
  const b=(await f.call('/jobs',{prompt:'B',request_id:'b',tool:'custom'})).value;
  assert.equal((await f.call('/worker/claim',{},true)).value.task.id,a.id);
  assert.equal((await f.call('/worker/claim',{},true)).value.task.id,a.id);
  const prefix='/worker/tasks/'+a.id;
  assert.equal((await f.call(prefix+'/snapshot',{status:'review',review_version:1},true)).status,409);
  await f.call(prefix+'/preview?v=1',png,true,'PUT');await f.call(prefix+'/snapshot',{status:'review',review_version:1,local_id:'localA'},true);
  assert.equal((await f.call('/jobs/'+a.id+'/review',{action:'end',version:1})).status,400);
  // No user action: local background detector completes and downloads normally.
  await f.call(prefix+'/result',png,true,'PUT');await f.call(prefix+'/snapshot',{status:'done',image_filename:'测试.png'},true);
  assert.equal((await f.call('/jobs/'+a.id)).value.status,'done');assert.equal((await f.call('/jobs/'+a.id+'/image')).status,200);
  assert.equal((await f.call('/jobs/'+a.id+'/review',{action:'end',version:1,confirmed:true})).status,409);
  assert.equal((await f.call('/worker/claim',{},true)).value.task.id,b.id);
  const bp='/worker/tasks/'+b.id;
  await f.call(bp+'/preview?v=2',png,true,'PUT');await f.call(bp+'/snapshot',{status:'review',review_version:2},true);
  assert.equal((await f.call('/jobs/'+b.id+'/review',{action:'wait',version:2})).status,202);
  const decision=(await f.call(bp+'/decision',undefined,true)).value.decision;assert.equal(decision.action,'wait');await f.call(bp+'/decision',decision,true);
  assert.equal((await f.call('/jobs/'+b.id+'/review',{action:'end',version:2,confirmed:true})).status,202);
  await f.call(bp+'/snapshot',{status:'not_generated'},true);
  assert.equal((await f.call('/jobs/'+b.id+'/image')).status,409);
  assert.equal((await f.call('/worker/claim',{},true)).value.task,null);
  assert.equal((await f.call('/history')).value.total,2);
 }finally{f.db.close()}
});
test('cloud: offline heartbeat and public monitor stay independent of tunnel',async()=>{
 const f=fixture();try{
  assert.equal((await f.call('/api/latest')).value.ready,false);
  assert.equal((await f.call('/api/latest')).value.worker_version,null);
  await f.call('/worker/heartbeat',{local_health:{worker_alive:true,automation_version:'v0.2.6'},kuma:{monitors:[{name:'server',status:1}]}},true);
  assert.equal((await f.call('/api/latest')).value.ready,true);
  assert.equal((await f.call('/api/latest')).value.worker_version,'v0.2.6');
  assert.equal((await f.call('/api/latest')).value.version,'cloud-1.0.4');
  assert.match((await f.call('/')).value,/cloud-1.0.4/);assert.doesNotMatch((await f.call('/')).value,/<iframe/);
  assert.equal((await f.call('/monitor')).status,200);
 }finally{f.db.close()}
});
