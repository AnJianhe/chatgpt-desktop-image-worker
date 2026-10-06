import {test} from 'node:test';
import assert from 'node:assert/strict';
import worker,{cleanUrl,publicState} from '../worker/index.js';
const db = {row:null,prepare(sql){return {bind(...v){this.v=v;return this},async run(){const [url,state,source_age,received_at]=this.v;db.row={url,state,source_age,received_at}},async first(){return db.row}}}};
const env={DB:db,LINK_SYNC_TOKEN:'test-only'};
const get=path=>worker.fetch(new Request('http://localhost'+path),env);
const update=data=>worker.fetch(new Request('http://localhost/api/update',{method:'POST',headers:{Authorization:'Bearer test-only'},body:JSON.stringify(data)}),env);
test('reject non-tunnel targets',()=>{for(const v of ['https://api.trycloudflare.com','https://api.trycloudflare.com/','http://cat.trycloudflare.com','https://cat.trycloudflare.com.evil.test','https://cat.trycloudflare.com@evil.test','https://cat.trycloudflare.com/path','https://cat.trycloudflare.com:443'])assert.equal(cleanUrl(v),'');});

test('provisioning API is hidden even if an old uploader already stored it',async()=>{
  const row={url:'https://api.trycloudflare.com',state:'可访问',source_age:1,received_at:Date.now()};
  assert.equal(publicState(row).url,'');assert.equal(publicState(row).ready,false);assert.equal(publicState({...row,state:'原地址重连中'}).candidate,false);
  db.row=row;const latest=await (await get('/api/latest')).json();assert.equal(latest.url,'');assert.equal(latest.candidate,false);
  assert.equal((await update({url:row.url,state:'可访问',source_age:1})).status,400);
});
test('unreported or stale service never redirects',()=>{assert.equal(publicState(null).ready,false);assert.equal(publicState({url:'https://cat.trycloudflare.com',state:'可访问',source_age:0,received_at:1},100000).ready,false);});

test('fresh reconnecting URL may bootstrap browser heartbeat but stopped or stale URLs cannot',()=>{
  const row={url:'https://cat.trycloudflare.com',state:'原地址重连中',source_age:1,received_at:1000};
  assert.equal(publicState(row,1001).ready,false);assert.equal(publicState(row,1001).candidate,true);
  for(const state of ['重启中','已停止','本地服务未就绪','状态已过期'])assert.equal(publicState({...row,state},1001).candidate,false);
  assert.equal(publicState({...row,source_age:50},1001).candidate,false);
  assert.equal(publicState(row,100000).candidate,false);
});
test('fixed entry embeds the app and never redirects; API follows new URL',async()=>{
  assert.equal((await get('/')).status,200);
  assert.equal((await worker.fetch(new Request('http://localhost/api/update',{method:'POST',body:'{}'}),env)).status,401);
  assert.equal((await update({url:'https://evil.test',state:'可访问',source_age:1})).status,400);
  await update({url:'https://first.trycloudflare.com',state:'可访问',source_age:1});
  let r=await get('/');assert.equal(r.status,200);assert.equal(r.headers.get('Location'),null);assert.match(r.headers.get('Cache-Control'),/no-store/);
  const html=await r.text();assert.match(html,/<iframe/);assert.match(html,/frame.src=url/);assert.doesNotMatch(html,/location\.(replace|assign)|window\.location/);
  new Function(html.match(/<script>([\s\S]*?)<\/script>/)[1]);
  await update({url:'https://next.trycloudflare.com',state:'重启中',source_age:1});assert.equal((await get('/')).status,200);
  await update({url:'https://next.trycloudflare.com',state:'可访问',source_age:1});assert.equal((await get('/')).headers.get('Location'),null);assert.equal((await (await get('/api/latest')).json()).url,'https://next.trycloudflare.com');
  await update({url:'https://next.trycloudflare.com',state:'可访问',source_age:50});assert.equal((await get('/')).status,200);
});
