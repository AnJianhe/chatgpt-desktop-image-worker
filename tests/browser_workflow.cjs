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
  const page=await browser.newPage(); const errors=[], bodies=[], reviews=[]; const jobs=new Map();
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://127.0.0.1:18977/**',async route=>{
   const request=route.request(),url=new URL(request.url()),p=url.pathname;
   const json=value=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(value)});
   if(p==='/')return route.fulfill({contentType:'text/html',body:html});
   if(p==='/static/workflow.js')return route.fulfill({contentType:'application/javascript',body:script});
   if(p==='/api/tunnel-heartbeat'){
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
  await page.selectOption('#fabricTool','custom');
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
  await page.waitForTimeout(2300);await page.locator('#taskList .task-card').last().click();
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
  assert.deepEqual(errors,[]);
  await page.reload();await page.waitForTimeout(100);
  assert.equal(await page.locator('#taskList .task-card').count(),3);
  assert.equal(bodies.length,3);
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.locator('#submitButton').isVisible(),true);
  console.log('PASS: custom literal prompt; 0/2/3 references; fourth rejection; independent deletion/re-add; A/B/C submissions; wait/retry/end/cancel; modal target isolation; history reload; mobile viewport; no JS errors.');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
