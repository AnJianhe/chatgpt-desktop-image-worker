"""Copy the existing UI into the Worker bundle; run before publishing UI changes."""
import json
from pathlib import Path
import re
import sys
app=Path(sys.argv[1]).resolve()
sys.path.insert(0,str(app))
from fabric_presets import FABRIC_CATALOG
html=(app/'templates/index.html').read_text(encoding='utf-8-sig')
html=html.replace('{{ fabric_catalog | tojson }}',json.dumps(FABRIC_CATALOG,ensure_ascii=False)).replace('{{ heartbeat_server_id | tojson }}','"cloud-v1"').replace('{{ entry_origin | tojson }}','null')
if re.search(r'{{|{%',html):raise ValueError('Unsupported template interpolation')
banner='''<div id="cloudWorkerState" role="status" style="padding:12px;text-align:center;background:#edf4ff;color:#244677">正在读取虚拟机状态…</div>'''
html=html.replace('<body>','<body>'+banner)
footer='''<footer style="padding:20px;text-align:center">云端版本 cloud-1.0.4 · <span id="desktopVersion">画图服务：正在读取版本</span> · <a href="/monitor">运行监控</a> · <a href="/report-bug">报告 Bug</a></footer><script>async function cloudState(){try{const r=await fetch('/api/latest',{cache:'no-store'});if(!r.ok)throw Error();const d=await r.json();document.getElementById('desktopVersion').textContent=d.ready?('画图服务：'+(d.worker_version||'尚未上报版本，请重启画图服务')):('画图服务离线；上次版本：'+(d.worker_version||'未知'));document.getElementById('cloudWorkerState').textContent=d.ready?'虚拟机在线，可以生成图片':'虚拟机暂未连接；可以提交任务，恢复后自动执行'}catch{document.getElementById('cloudWorkerState').textContent='云端连接暂时中断，正在重试'}}cloudState();setInterval(cloudState,5000);</script>'''
html=html.replace('</body>',footer+'</body>')
js=(app/'static/workflow.js').read_text(encoding='utf-8-sig').replace('image-helper-workflow-v2','image-helper-cloud-v1')
root=Path(__file__).resolve().parent.parent/'worker'
(root/'assets.js').write_text('export const page='+json.dumps(html,ensure_ascii=False)+';\nexport const workflow='+json.dumps(js,ensure_ascii=False)+';\nexport const catalog='+json.dumps(FABRIC_CATALOG,ensure_ascii=False)+';\n',encoding='utf-8')
print('Workflow assets synchronized')
