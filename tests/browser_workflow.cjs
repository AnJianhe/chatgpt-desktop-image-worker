// Run with PW_MODULE pointing to an installed playwright package and WORKFLOW_HTML
// pointing to a Flask-rendered fixture. Mock HTTP only: no ChatGPT/desktop actions.
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {chromium}=require(process.env.PW_MODULE || 'playwright');
const html=fs.readFileSync(process.env.WORKFLOW_HTML,'utf8');
const script=fs.readFileSync(path.join(__dirname,'../app/static/workflow.js'),'utf8');
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a7UAAAAAASUVORK5CYII=','base64');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try {
  const page=await browser.newPage(); const errors=[], bodies=[], reviews=[]; const jobs=new Map();let heartbeatFail=false;
  await page.addInitScript(()=>{
    window.spoken=[];
    window.SpeechSynthesisUtterance=function(text){this.text=text;};
    Object.defineProperty(window,'speechSynthesis',{value:{getVoices:()=>[{lang:'zh-CN'}],cancel:()=>{},speak:u=>window.spoken.push(u.text)}});
  });
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://127.0.0.1:18977/**',async route=>{
   const request=route.request(),url=new URL(request.url()),p=url.pathname;
   const json=value=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(value)});
   if(p==='/')return route.fulfill({contentType:'text/html',body:html});
   if(p==='/static/workflow.js')return route.fulfill({contentType:'application/javascript',body:script});
   if(p==='/api/tunnel-heartbeat'){
    if(heartbeatFail)return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'连接失败'})});
    const data=request.method()==='POST'?request.postDataJSON():{};
    return json({status:'ok',nonce:data.nonce,server_id:data.server_id});
   }
   if(p==='/history'){
    const all=[...jobs.values()].filter(j=>!url.searchParams.get('status')||j.status===url.searchParams.get('status')).reverse();
    return json({items:all.slice(Number(url.searchParams.get('offset')||0),Number(url.searchParams.get('offset')||0)+20),total:all.length});
   }
   if(p==='/uploads'){
    const id=request.postData().match(/name="upload_id"\r\n\r\n([^\r]+)/)[1];return json({upload_id:id});
   }
   if(p==='/jobs'){
    const body=request.postDataJSON();bodies.push(body);
    const id='task'+bodies.length;
    jobs.set(id,{id,created_at:Date.now()/1000,status:bodies.length===1?'running':'queued',prompt:body.prompt,referenceImages:body.upload_ids,stage:'test'});
    return json({id,status:jobs.get(id).status});
   }
   const parts=p.split('/'),id=parts[2],job=jobs.get(id);
   if(parts[3]==='review'){
    const body=request.postDataJSON();reviews.push({id,...body});
    job.status=body.action==='end'?'not_generated':'running';return json({id,status:job.status});
   }
   if(parts[3]==='preview'||parts[3]==='image')return route.fulfill({contentType:'image/png',body:png});
   if(job)return json(job);
   return route.fulfill({status:404,body:'{}'});
  });
  await page.goto('http://127.0.0.1:18977/');
  await page.selectOption('#fabricTool','edit');
  await page.fill('#prompt','修改图片');await page.click('#submitButton');
  assert.match(await page.textContent('#attentionText'),/需要先上传/);
  assert.equal(await page.evaluate(()=>document.body.classList.contains('needs-attention')),true);
  assert.match(await page.evaluate(()=>window.spoken.at(-1)),/请上传参考图片/);
  assert.equal(bodies.length,0);
  await page.selectOption('#fabricTool','custom');
  await page.fill('#prompt','');
  assert.equal(await page.inputValue('#prompt'),'');
  assert.equal(await page.locator('#applyPreset').isVisible(),false);
  const literal='  建筑摄影\n任意提示词，沿用参考图配色；无参考图时采用协调自然配色。  ';
  await page.fill('#prompt',literal);await page.click('#submitButton');
  await page.waitForFunction(()=>document.querySelectorAll('#taskList .task-card').length===1);
  assert.equal(await page.locator('#prompt').isEnabled(),true);
  const file=n=>({name:n+'.png',mimeType:'image/png',buffer:png});
  await page.setInputFiles('#referenceFile',[file(1),file(2),file(3)]);
  assert.equal(await page.locator('#referencePreviews .reference-card').count(),3);
  await page.setInputFiles('#referenceFile',file(4));
  assert.match(await page.textContent('#errorMessage'),/最多 3/);
  assert.match(await page.textContent('#attentionText'),/最多 3/);
  assert.equal(await page.locator('#referencePreviews .reference-card').count(),3);
  await page.locator('#referencePreviews button').nth(1).click();
  assert.equal(await page.locator('#referencePreviews .reference-card').count(),2);
  await page.setInputFiles('#referenceFile',file(4));
  assert.equal(await page.locator('#referencePreviews .reference-card').count(),3);
  await page.fill('#prompt','任务B');await page.click('#submitButton');
  await page.locator('#referencePreviews button').nth(0).click();
  await page.fill('#prompt','任务C');await page.click('#submitButton');
  await page.waitForFunction(()=>document.querySelectorAll('#taskList .task-card').length===3);
  await page.waitForTimeout(500);
  assert.equal(bodies.length,3);assert.equal(bodies[0].prompt,literal);
  assert.equal(bodies[1].prompt,'任务B');assert.equal(bodies[1].upload_ids.length,3);
  assert.equal(bodies[2].prompt,'任务C');assert.equal(bodies[2].upload_ids.length,2);
  assert.deepEqual(bodies[2].upload_ids,bodies[1].upload_ids.slice(1));
  const a=jobs.get('task1');Object.assign(a,{status:'review',review_version:1,preview_url:'/jobs/task1/preview?v=1'});
  await page.waitForTimeout(2300);
  assert.match(await page.textContent('#attentionText'),/需要人工确认/);
  assert.equal(await page.locator('#viewAttentionTask').isVisible(),true);
  const spokenCount=await page.evaluate(()=>window.spoken.length);
  await page.waitForTimeout(2300);
  assert.equal(await page.evaluate(()=>window.spoken.length),spokenCount);
  await page.click('#viewAttentionTask');
  await page.click('#endGeneration');
  assert.equal(reviews.length,0);assert.equal(a.status,'review');
  await page.click('#cancelEnd');assert.equal(reviews.length,0);assert.equal(a.status,'review');
  await page.click('#keepWaiting');await page.waitForTimeout(100);
  assert.equal(reviews[0].action,'wait');
  Object.assign(a,{status:'review',review_version:2});await page.waitForTimeout(2300);
  await page.click('#retryDownload');await page.waitForTimeout(100);assert.equal(reviews[1].action,'retry');
  Object.assign(a,{status:'review',review_version:3});await page.waitForTimeout(2300);
  await page.click('#endGeneration');
  // Switching selection must never terminate the wrong task.
  await page.evaluate(()=>document.querySelector('#taskList .task-card').click());
  await page.click('#confirmEnd');await page.waitForTimeout(100);
  assert.deepEqual(reviews[2],{id:'task1',action:'end',version:3,confirmed:true});
  assert.equal(a.status,'not_generated');assert.equal(jobs.get('task2').status,'queued');
  await page.click('#refreshHistory');await page.waitForTimeout(100);
  assert.equal(await page.locator('#historyList article').count(),3);
  await page.selectOption('#historyFilter','not_generated');await page.waitForTimeout(100);
  assert.equal(await page.locator('#historyList article').count(),1);
  await page.locator('#historyList button').click();
  assert.match(await page.textContent('#statusBadge'),/未生成/);
  const stillActive=jobs.get('task2');Object.assign(stillActive,{status:'review',review_version:1,preview_url:'/jobs/task2/preview?v=1'});await page.waitForTimeout(2300);
  await page.locator('#taskList .task-card').nth(1).click();
  await page.click('#endGeneration');
  Object.assign(stillActive,{status:'done',image_url:'/jobs/task2/image'});await page.waitForTimeout(2300);
  assert.equal(await page.locator('#endDialog').isVisible(),false);
  assert.equal(await page.locator('#attentionBanner').isVisible(),false);
  const c=jobs.get('task3');Object.assign(c,{status:'error',error:'下载文件失败'});await page.waitForTimeout(2300);
  assert.match(await page.textContent('#attentionText'),/下载文件失败/);
  await page.waitForTimeout(2300);
  assert.equal(await page.locator('#attentionBanner').isVisible(),true);
  await page.click('#dismissAttention');
  assert.equal(await page.locator('#attentionBanner').isVisible(),false);
  const beforeMute=await page.evaluate(()=>window.spoken.length);
  await page.uncheck('#voiceEnabled');await page.click('#repeatAttention');
  assert.equal(await page.evaluate(()=>window.spoken.length),beforeMute);
  await page.check('#voiceEnabled');
  assert.equal(await page.evaluate(()=>window.spoken.length),beforeMute+1);
  heartbeatFail=true;
  await page.waitForFunction(()=>document.querySelector('#attentionText').textContent.includes('连接暂时出现问题'),null,{timeout:20000});
  const outageCount=await page.evaluate(()=>window.spoken.length);
  await page.waitForTimeout(5300);
  assert.equal(await page.evaluate(()=>window.spoken.length),outageCount);
  heartbeatFail=false;
  await page.waitForFunction(()=>document.querySelector('#attentionBanner').hidden,null,{timeout:8000});
  assert.equal(reviews.length,3);
  assert.deepEqual(errors,[]);
  await page.reload();await page.waitForTimeout(100);
  assert.equal(await page.locator('#taskList .task-card').count(),3);
  assert.equal(bodies.length,3);
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.locator('#submitButton').isVisible(),true);
  await page.click('#dismissAttention');
  await page.selectOption('#fabricTool','edit');await page.fill('#prompt','修改');await page.click('#submitButton');
  await page.screenshot({path:process.env.ALERT_SCREENSHOT||'work/voice-alert-mobile.png',fullPage:true});
  console.log('PASS: workflow regression; required-reference red frame and voice; review of unselected task; poll speech deduplication; auto-completion clears alert; persistent error and acknowledgement; voice mute/unmute; connection warning and recovery; mobile viewport; no JS errors.');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
