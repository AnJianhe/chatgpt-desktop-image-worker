(function () {
  'use strict';
  const terminal = new Set(['done', 'error', 'not_generated']);
  const names = {pending:'正在提交', queued:'排队中', running:'正在执行', review:'等待人工确认', done:'图片生成成功', not_generated:'本次未生成图片', error:'程序执行失败'};
  const uid = () => {
    if(globalThis.crypto?.randomUUID)return crypto.randomUUID().replaceAll('-', '');
    const bytes=new Uint8Array(16);
    if(globalThis.crypto?.getRandomValues)crypto.getRandomValues(bytes);else for(let i=0;i<16;i++)bytes[i]=Math.floor(Math.random()*256);
    return Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');
  };
  function addReferences(current, files, makeURL) {
    if (current.length + files.length > 3) throw new Error('每个任务最多 3 张参考图片，已达到上限。请先删除图片再添加。');
    if (files.some(f => f.size > 20*1024*1024 || !['image/png','image/jpeg','image/webp'].includes(f.type))) throw new Error('请选择不超过 20 MB 的 PNG、JPEG 或 WEBP 图片。');
    return [...current, ...files.map(file => ({key:uid(), file, url:makeURL(file), uploadId:null}))];
  }
  function snapshotDraft(prompt, tool, references) {
    const requestId=uid();
    return {taskId:requestId, result:null, request_id:requestId, prompt, tool, referenceImages:references.map(r => ({...r})), upload_ids:[], status:'pending', id:null, stage:'正在提交', error:null};
  }
  function presetPrompt(catalog, tool, layout, theme, palette, hasReference) {
    if (tool.id === 'custom') return '';
    if (tool.id !== 'fabric') return tool.id === 'seamless' && !hasReference
      ? tool.instruction.replace('根据参考图或文字', '根据文字').replace('保留主要纹样、配色与风格', '采用协调自然的纹样、配色与风格') : tool.instruction;
    const chosenPalette = palette === catalog.palettes[0] ? (hasReference ? '沿用参考图配色' : '采用协调自然配色') : palette;
    const chosenTheme = theme.id === 'reference' && !hasReference ? '根据文字要求原创纹样，造型清晰，配色协调。' : theme.instruction;
    return [layout.instruction, '纹样主题：'+chosenTheme, '配色：'+chosenPalette+'。', catalog.base, hasReference ? catalog.reference : ''].filter(Boolean).join('\n\n');
  }
  if (typeof module !== 'undefined') module.exports = {addReferences, snapshotDraft, presetPrompt, terminal};
  if (typeof document === 'undefined') return;
  const config = window.workflowConfig;
  const catalog = config.catalog;
  const $ = id => document.getElementById(id);
  const storageKey = 'image-helper-workflow-v2';
  let references = [], tasks = [], selected = null, modalTarget = null, submittingChain = Promise.resolve();
  let heartbeatId = config.serverId, heartbeatBusy = false, polling = false, ready = window.parent === window;
  let generatedPreset = null;
  [['fabricTool',catalog.tools],['fabricLayout',catalog.layouts],['fabricTheme',catalog.themes]].forEach(([id, rows]) => rows.forEach(r => $(id).add(new Option(r.label, r.id))));
  catalog.palettes.forEach(p => $('fabricPalette').add(new Option(p,p)));
  $('fabricTool').value = 'fabric';
  function persist() {
    const state = {schema:2, tasks:tasks.map(t => ({...t, referenceImages:t.referenceImages.map(r => ({key:r.key, uploadId:r.uploadId, name:r.file?.name || r.name}))})), selected, draft:{prompt:$('prompt').value,tool:$('fabricTool').value}};
    try { sessionStorage.setItem(storageKey, JSON.stringify(state)); } catch (_) {}
    if (ready && config.entryOrigin && window.parent !== window) window.parent.postMessage({type:'fabric-task-state',state},config.entryOrigin);
  }
  function error(message) { $('errorMessage').textContent = message || ''; $('errorMessage').hidden = !message; }
  async function request(path, options={}, timeout=15000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(path,{...options,signal:controller.signal,cache:'no-store'});
      const data = await response.json();
      if (!response.ok) { const e = new Error(data.error || '请求失败'); e.status=response.status; throw e; }
      return data;
    } finally { clearTimeout(timer); }
  }
  const post = (path,data) => request(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  function applyPreset() {
    const tool = catalog.tools.find(r => r.id === $('fabricTool').value);
    if (tool.id === 'custom') { generatedPreset=null; render(); persist(); return; }
    $('prompt').value = presetPrompt(catalog,tool,catalog.layouts.find(r=>r.id===$('fabricLayout').value),catalog.themes.find(r=>r.id===$('fabricTheme').value),$('fabricPalette').value,references.length>0);
    generatedPreset = $('prompt').value;
    render(); persist();
  }
  function referenceChanged() {
    if (generatedPreset !== null && $('prompt').value === generatedPreset) applyPreset();
    renderReferences(); render();
  }
  function renderReferences() {
    $('referencePreviews').replaceChildren();
    references.forEach((r,index) => {
      const card=document.createElement('div'); card.className='reference-card';
      const img=document.createElement('img'); img.src=r.url; img.alt='参考图片 '+(index+1);
      const label=document.createElement('span'); label.textContent=r.file.name;
      const button=document.createElement('button'); button.type='button'; button.className='secondary'; button.textContent='删除第 '+(index+1)+' 张';
      button.onclick=()=>{ URL.revokeObjectURL(r.url); references=references.filter(x=>x.key!==r.key); referenceChanged(); };
      card.append(img,label,button); $('referencePreviews').append(card);
    });
    $('referenceName').textContent = references.length+' / 3 张 · 每张最大 20 MB，可单独删除并重新添加。';
  }
  $('referenceFile').onchange=()=>{
    try { references=addReferences(references,Array.from($('referenceFile').files),f=>URL.createObjectURL(f)); error(''); referenceChanged(); }
    catch(e) { error(e.message); }
    $('referenceFile').value='';
  };
  function current() { return tasks.find(t=>t.request_id===selected); }
  function render() {
    const tool=catalog.tools.find(r=>r.id===$('fabricTool').value);
    document.querySelector('.preset-grid').hidden=tool.id!=='fabric';
    document.querySelector('.examples').hidden=tool.id==='custom';
    $('applyPreset').hidden=tool.id==='custom';
    $('toolHint').textContent=tool.id==='custom'?'纯自定义模式：输入内容原样发送，参考图片可选。':tool.requires_reference?'此工具必须上传至少一张参考图片。':'没有参考图片也可直接按文字生成。';
    $('characterCount').textContent=Array.from($('prompt').value).length+' 字';
    $('submitButton').disabled=!$('prompt').value.trim();
    $('submitLabel').textContent='提交新任务';
    $('taskList').replaceChildren();
    tasks.slice().reverse().forEach(t=>{
      const button=document.createElement('button'); button.type='button'; button.className='secondary task-card';
      button.textContent=(t===current()?'● ':'')+(names[t.status]||t.status)+' · '+t.prompt.slice(0,45)+' · '+t.referenceImages.length+' 张参考图';
      button.onclick=()=>{selected=t.request_id; render(); persist();}; $('taskList').append(button);
      if (t.status==='pending' && t.submitError) {
        const retry=document.createElement('button'); retry.type='button'; retry.className='secondary'; retry.textContent='恢复提交此任务';
        retry.onclick=()=>enqueueSubmit(t); $('taskList').append(retry);
      }
    });
    const t=current();
    $('statusBadge').textContent=t ? names[t.status] : '等待任务';
    $('statusBadge').dataset.status=t?.status||'idle';
    $('stageText').textContent=t?.submitError||t?.stage||'提交后可以立即创建下一个任务。';
    $('taskId').hidden=!t?.id; $('taskId').textContent=t?.id||'';
    $('queueText').hidden=!t?.queue_position; $('queueText').textContent='队列位置 '+(t?.queue_position||'');
    $('reviewActions').hidden=t?.status!=='review';
    $('resultActions').hidden=!['done','review'].includes(t?.status);
    $('downloadLink').hidden=t?.status!=='done';
    $('placeholder').hidden=['done','review'].includes(t?.status);
    $('resultImage').hidden=!['done','review'].includes(t?.status);
    const src=t?.status==='review'?t.preview_url:t?.status==='done'?t.image_url:'';
    if (src && $('resultImage').getAttribute('src')!==src) $('resultImage').src=src;
    if (!src) $('resultImage').removeAttribute('src');
    $('previewTitle').textContent=t?.status==='not_generated'?'本次未生成图片':t?.status==='error'?'程序执行失败':'等待生成结果';
    $('previewDescription').textContent=t?.error||t?.stage||'选择任务查看各自进度。';
    if(t?.image_url){$('downloadLink').href=t.image_url; $('downloadLink').download=t.image_filename||'生成图片.png';}
  }
  function enqueueSubmit(t) {
    if(t.submitting || t.id || terminal.has(t.status)) return;
    t.submitting=true; t.submitError=null;
    // Only network submission ordering is chained; the composer always stays available.
    submittingChain=submittingChain.then(()=>submitTask(t)).catch(()=>{});
  }
  async function submitTask(t) {
    try {
      for (const r of t.referenceImages) {
        if(r.uploadId) continue;
        if(!r.file) throw new Error('页面已重新加载，未上传的参考图片无法恢复。请重新选择图片并提交新任务。');
        const form=new FormData();form.append('image',r.file);form.append('upload_id',r.key);
        const data=await request('/uploads',{method:'POST',body:form},120000); r.uploadId=data.upload_id; persist();
      }
      t.upload_ids=t.referenceImages.map(r=>r.uploadId); persist();
      const data=await post('/jobs',{prompt:t.prompt,tool:t.tool,request_id:t.request_id,upload_ids:t.upload_ids});
      t.id=data.id; t.taskId=data.id; t.status=data.status; t.stage='任务已接收';
      t.referenceImages.forEach(r=>{r.name=r.file?.name||r.name;r.file=null;});
    } catch(e) {
      if(e.status && e.status<500 && e.status!==429){t.status='error';t.error=e.message;}
      else t.submitError=e.message+'；可恢复提交，使用相同编号防止重复生成。';
    } finally {t.submitting=false;persist();render();}
  }
  $('promptForm').onsubmit=event=>{
    event.preventDefault(); const tool=catalog.tools.find(r=>r.id===$('fabricTool').value);
    const prompt=$('prompt').value;
    if(!prompt.trim()) return;
    if(tool.requires_reference && !references.length){error('此工具需要先上传参考图片。');return;}
    const t=snapshotDraft(prompt,tool.id,references);tasks.push(t);selected=t.request_id;
    error('');persist();render();enqueueSubmit(t);
  };
  async function poll() {
    if(polling)return;polling=true;
    try { await Promise.all(tasks.filter(t=>t.id&&!terminal.has(t.status)).map(async t=>{
      try {const data=await request('/jobs/'+encodeURIComponent(t.id));const {referenceImages, ...progress}=data;Object.assign(t,progress);t.networkError=null;}
      catch(e){t.networkError=e.message;if(e.status===404){t.status='error';t.error='服务器中已无此任务记录，请重新提交。';}}
    }));persist();render(); } finally {polling=false;}
  }
  async function decide(target,action,confirmed=false) {
    const t=tasks.find(t=>t.request_id===target.requestId);
    if(!t || t.status!=='review' || t.review_version!==target.version){error('该截图已更新，请重新选择操作。');return;}
    if(t.deciding)return;t.deciding=true;
    try {await post('/jobs/'+encodeURIComponent(t.id)+'/review',{action,version:target.version,confirmed});t.status='running';t.stage=action==='end'?'正在结束任务':action==='retry'?'立即尝试下载':'继续检测完成标记';persist();render();await poll();}
    catch(e){error(e.message);await poll();}finally{t.deciding=false;}
  }
  function target() {const t=current();return t?.status==='review'?{requestId:t.request_id,version:t.review_version}:null;}
  $('retryDownload').onclick=()=>{const v=target();if(v)decide(v,'retry');};
  $('keepWaiting').onclick=()=>{const v=target();if(v)decide(v,'wait');};
  $('endGeneration').onclick=()=>{modalTarget=target();if(modalTarget)$('endDialog').showModal();};
  $('cancelEnd').onclick=()=>{$('endDialog').close();modalTarget=null;};
  $('confirmEnd').onclick=()=>{const v=modalTarget;$('endDialog').close();modalTarget=null;if(v)decide(v,'end',true);};
  $('endDialog').addEventListener('cancel',()=>{modalTarget=null;});
  $('reloadImage').hidden=false; $('reloadImage').onclick=()=>{const t=current();const src=t?.status==='review'?t.preview_url:t?.image_url;if(src)$('resultImage').src=src+(src.includes('?')?'&':'?')+'reload='+Date.now();};
  $('fabricTool').onchange=()=>{if($('fabricTool').value==='custom' && generatedPreset!==null && $('prompt').value===generatedPreset)$('prompt').value='';generatedPreset=null;render();persist();};
  $('applyPreset').onclick=applyPreset;
  $('prompt').oninput=()=>{generatedPreset=null;render();persist();};
  document.querySelectorAll('.example').forEach(button=>button.onclick=()=>{$('fabricTheme').value=button.dataset.theme;$('fabricTool').value='fabric';applyPreset();});
  async function heartbeat() {
    if(heartbeatBusy)return;heartbeatBusy=true;const nonce=uid();
    try {
      const data=await post('/api/tunnel-heartbeat',{nonce,server_id:heartbeatId});
      if(data.nonce!==nonce||data.server_id!==heartbeatId)throw new Error('连接校验失败');
      $('connectionText').textContent='连接正常';
      if(config.entryOrigin && window.parent!==window)window.parent.postMessage({type:'fabric-app-heartbeat'},config.entryOrigin);
    } catch(e) {
      $('connectionText').textContent='正在重新连接';
      if(e.status===409)try{const data=await request('/api/tunnel-heartbeat');heartbeatId=data.server_id;}catch(_){}
    } finally {heartbeatBusy=false;}
  }
  function restore(state) {
    if(!state || typeof state!=='object')return;
    if(state.schema===2 && Array.isArray(state.tasks)) {
      const known=new Set(tasks.map(t=>t.request_id));
      for(const t of state.tasks){if(!t?.request_id||known.has(t.request_id))continue;tasks.push({...t,referenceImages:Array.isArray(t.referenceImages)?t.referenceImages:[],submitting:false,deciding:false});}
      selected=selected||state.selected;
      if(!tasks.some(t=>t.request_id===selected)) selected=tasks[0]?.request_id||null;
      if(!$('prompt').value && state.draft){$('prompt').value=state.draft.prompt||'';if(catalog.tools.some(t=>t.id===state.draft.tool))$('fabricTool').value=state.draft.tool;}
    } else if(state.id && !tasks.some(t=>t.id===state.id)) {
      const t={...state,taskId:state.id,request_id:state.id,referenceImages:[],image_url:state.status==='done'?'/jobs/'+encodeURIComponent(state.id)+'/image':undefined,image_filename:state.imageFilename||'',preview_url:state.previewURL,review_version:state.reviewVersion};tasks.push(t);selected=t.request_id;
    }
    tasks.filter(t=>t.status==='pending'&&!t.id).forEach(t=>{t.submitError='上次提交未完成，请选择恢复提交。';});render();poll();
  }
  window.addEventListener('message',event=>{
    if(event.source!==window.parent||event.origin!==config.entryOrigin||event.data?.type!=='fabric-resume-state')return;
    ready=true;restore(event.data.state);persist();
  });
  try{restore(JSON.parse(sessionStorage.getItem(storageKey)||sessionStorage.getItem('image-helper-current-task-v1')||'null'));}catch(_){}
  if(!$('prompt').value)applyPreset();
  renderReferences();render();heartbeat();setInterval(heartbeat,5000);setInterval(poll,2000);
  window.addEventListener('online',()=>{heartbeat();poll();});
})();
