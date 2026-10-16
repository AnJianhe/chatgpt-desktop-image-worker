export const bugCategories = {chatgpt:'ChatGPT 问题',vm:'虚拟机问题',sites:'Sites 网页问题',unknown:'暂不确定'};
export async function bugs(request,env,ctx){
 const {sql,owner,agent,body,json,fail}=ctx,url=new URL(request.url),path=url.pathname,method=request.method;
 const admin=!!env.BUG_ADMIN_TOKEN&&request.headers.get('Authorization')===`Bearer ${env.BUG_ADMIN_TOKEN}`;
 if(path.startsWith('/admin/bugs')&&!admin)fail('无权查看修复台',401);
 const publicReport=r=>({id:r.id,category:r.category,description:r.description,task_id:r.task_id,error:r.error,status:r.status,reply:r.reply,created:r.created,updated:r.updated,delivered:r.delivered});
 if(path==='/bugs'&&method==='POST'){
  const d=await body(request);
  d.category='unknown';
  if(typeof d.description!=='string'||!d.description.trim()||d.description.length>4000)fail('请填写不超过4000字的问题描述');
  if(typeof d.request_id!=='string'||! /^[a-f0-9]{32}$/.test(d.request_id))fail('报告编号无效');
  let taskId=null,error=String(d.error||'').slice(0,4000);
  if(d.task_id){const t=await sql('SELECT id,snapshot FROM cloud_tasks WHERE id=? AND owner=?',d.task_id,owner).first();if(!t)fail('关联任务不存在',404);taskId=t.id;error=JSON.parse(t.snapshot).error||error;}
  const existing=await sql('SELECT * FROM bug_reports WHERE owner=? AND request_id=?',owner,d.request_id).first();
  if(existing){if(existing.category!==d.category||existing.description!==d.description.trim()||existing.task_id!==taskId||existing.error!==error)fail('报告编号重复',409);return json({report:publicReport(existing),message:'我们已经收到 Bug 报告'},200);}
  const reportId=crypto.randomUUID().replaceAll('-',''),now=Date.now();
  await sql('INSERT INTO bug_reports(id,owner,request_id,category,description,task_id,error,status,reply,created,updated,delivered) VALUES(?,?,?,?,?,?,?,\'new\',\'\',?,?,NULL)',reportId,owner,d.request_id,d.category,d.description.trim(),taskId,error,now,now).run();
  return json({report:publicReport(await sql('SELECT * FROM bug_reports WHERE id=?',reportId).first()),message:'我们已经收到 Bug 报告'},201);
 }
 if(/^\/bugs\/[a-f0-9]{32}$/.test(path)&&method==='GET'){
  const r=await sql('SELECT * FROM bug_reports WHERE id=? AND owner=?',path.split('/')[2],owner).first();if(!r)fail('报告不存在',404);return json({report:publicReport(r)});
 }
 if(path==='/worker/bugs'&&method==='GET'){
  if(!agent)fail('Unauthorized',401);
  const rows=await sql("SELECT * FROM bug_reports WHERE status!='fixed' ORDER BY (delivered IS NULL) DESC,created DESC LIMIT 100").all();return json({items:rows.results.map(publicReport)});
 }
 if(/^\/worker\/bugs\/[a-f0-9]{32}\/receipt$/.test(path)&&method==='POST'){
  if(!agent)fail('Unauthorized',401);const reportId=path.split('/')[3];
  const r=await sql('SELECT id FROM bug_reports WHERE id=?',reportId).first();if(!r)fail('报告不存在',404);
  await sql('UPDATE bug_reports SET delivered=COALESCE(delivered,?) WHERE id=?',Date.now(),reportId).run();return json({ok:true});
 }
 if(path==='/admin/bugs'&&method==='GET'){
  const offset=Math.max(0,Number(url.searchParams.get('offset'))||0);
  const rows=await sql('SELECT * FROM bug_reports ORDER BY created DESC LIMIT 100 OFFSET ?',offset).all();return json({items:rows.results.map(publicReport)});
 }
 if(/^\/admin\/bugs\/[a-f0-9]{32}$/.test(path)&&method==='POST'){
  const d=await body(request);if(!Object.hasOwn(bugCategories,d.category)||!['new','triage','fixed'].includes(d.status)||typeof d.reply!=='string'||d.reply.length>2000)fail('处理状态或回复无效');
  const reportId=path.split('/')[3];if(!await sql('SELECT id FROM bug_reports WHERE id=?',reportId).first())fail('报告不存在',404);
  await sql('UPDATE bug_reports SET status=?,reply=?,category=?,updated=? WHERE id=?',d.status,d.reply,d.category,Date.now(),reportId).run();return json({ok:true});
 }
 if(/^\/admin\/bugs\/[a-f0-9]{32}\/log$/.test(path)&&method==='GET'){
  const r=await sql('SELECT task_id FROM bug_reports WHERE id=?',path.split('/')[3]).first();if(!r?.task_id)fail('此报告没有关联生成任务',404);
  const obj=await env.BUCKET.get('logs/'+r.task_id);if(!obj)fail('日志尚未同步，完整日志仍保留在虚拟机',404);
  return new Response(obj.body,{headers:{'Content-Type':'application/x-ndjson; charset=utf-8','Cache-Control':'no-store','Content-Disposition':'attachment; filename="generation-log.jsonl"'}});
 }
 return null;
}
