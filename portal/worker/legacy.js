export const FRESH_MS = 90000;
const headers = {"Cache-Control":"no-store, max-age=0", "X-Content-Type-Options":"nosniff"};
const states = new Set(["可访问","原地址重连中","连接中","本地服务未就绪","等待地址","重启中","已停止","启动重试中","状态已过期"]);
export function cleanUrl(value) {
  if (typeof value !== "string" || !/^https:\/\/(?!api\.)[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com\/?$/.test(value)) return "";
  return value.replace(/\/$/, "");
}
function database(env) {
  if (!env.DB?.prepare) throw new Error("Missing DB binding");
  return env.DB;
}
export function publicState(row, now = Date.now()) {
  const url = cleanUrl(row?.url);
  const fresh = row && now >= row.received_at && now - row.received_at < FRESH_MS;
  return {url, ready: Boolean(fresh && url && row.state === "可访问" && row.source_age <= 45),
    candidate: Boolean(fresh && url && row.source_age <= 45 && ["可访问","原地址重连中","连接中","等待地址"].includes(row.state)),
    state: fresh ? row.state : "状态已过期", updated_at: row?.received_at || null};
}
const shellPage = String.raw`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>布料花型助手</title><link rel="icon" href="/favicon.svg" type="image/svg+xml"><style>
*{box-sizing:border-box}html,body{margin:0;height:100%;background:#f6f4ef;color:#263d36;font:15px/1.7 system-ui,"Microsoft YaHei",sans-serif}[hidden]{display:none!important}header{height:44px;display:flex;align-items:center;gap:16px;padding:0 18px;background:white;border-bottom:1px solid #e2e4dc}header strong{font-size:14px;white-space:nowrap}#connection{margin-left:auto;color:#6b7871;font-size:13px}#reconnect{border:0;background:#edf4ed;color:#327856;padding:3px 10px;border-radius:6px;cursor:pointer}main{position:relative;height:calc(100dvh - 44px)}iframe{display:block;width:100%;height:100%;border:0;background:#f6f4ef}#waiting{position:absolute;inset:0;display:grid;place-items:center;background:#f6f4eff5;padding:20px}.card{width:min(500px,100%);padding:38px 28px;border:1px solid #e2e4dc;border-radius:20px;background:white;text-align:center;box-shadow:0 12px 45px #193d2410}.mark{font-size:40px;color:#327856}h1{font-size:23px;margin:14px 0 8px}p{color:#6b7871;margin:8px 0}.small{font-size:13px}#retry{margin:18px 0 8px;border:0;background:#2c7252;color:white;border-radius:10px;padding:10px 24px;font:inherit;cursor:pointer}
</style></head><body><header><strong>✿ 布料花型助手</strong><span id="connection" role="status">正在连接…</span><button id="reconnect">重新连接</button></header><main><iframe id="app" title="布料花型设计工作台" sandbox="allow-scripts allow-same-origin allow-forms allow-downloads" hidden></iframe><div id="waiting"><div class="card"><span class="mark">✿</span><h1>画图服务正在连接</h1><p id="status" role="status" aria-live="polite">正在检查最新连接…</p><p>恢复后会自动显示工作台。</p><button id="retry">立即检查</button><p class="small">可以直接刷新或收藏当前网址。</p></div></div></main><script>
const frame=document.getElementById('app'), waiting=document.getElementById('waiting'), connection=document.getElementById('connection'), status=document.getElementById('status');
const storageKey='fabric-entry-current-task-v1';
let checking=false,currentUrl='',latestReady=false,frameHeartbeat=0,loadedAt=0,supportsHeartbeat=false,resumeSent=false,savedState=null;
try{savedState=JSON.parse(sessionStorage.getItem(storageKey)||'null')}catch{}
function showWaiting(text){waiting.hidden=false;status.textContent=text;connection.textContent='正在恢复连接';}
function showApp(){waiting.hidden=true;frame.hidden=false;connection.textContent='连接正常';}
function loadApp(url){currentUrl=url;frameHeartbeat=0;loadedAt=Date.now();supportsHeartbeat=false;resumeSent=false;frame.hidden=false;showWaiting('正在加载工作台…');frame.src=url+'/';}
frame.addEventListener('load',()=>{if(latestReady&&!supportsHeartbeat)showApp()});
window.addEventListener('message',event=>{
  if(!currentUrl||event.source!==frame.contentWindow||event.origin!==currentUrl)return;
  const data=event.data;if(!data||typeof data!=='object')return;
  if(data.type==='fabric-app-heartbeat'){
    frameHeartbeat=Date.now();supportsHeartbeat=true;showApp();
    if(!resumeSent){resumeSent=true;frame.contentWindow.postMessage({type:'fabric-resume-state',state:savedState},currentUrl);}
  }else if(data.type==='fabric-task-state'&&data.state&&typeof data.state==='object'){
    try{const raw=JSON.stringify(data.state);if(raw.length<=100000){savedState=data.state;sessionStorage.setItem(storageKey,raw)}}catch{}
  }
});
async function check(force=false){
  if(checking)return;checking=true;const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),6000);
  try{
    const response=await fetch('/api/latest',{cache:'no-store',signal:controller.signal});if(!response.ok)throw Error();const data=await response.json();
    latestReady=data.ready===true&&/^https:\/\/(?!api\.)[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com$/.test(data.url);
    const candidate=data.candidate===true&&/^https:\/\/(?!api\.)[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com$/.test(data.url);
    if(latestReady||candidate){
      if(data.url!==currentUrl||force||(!latestReady&&(!frameHeartbeat||Date.now()-frameHeartbeat>15000)&&Date.now()-loadedAt>=15000))loadApp(data.url);
      else if(supportsHeartbeat&&Date.now()-frameHeartbeat>15000)showWaiting('连接暂时中断，正在自动恢复。');
    }else if(!frameHeartbeat||Date.now()-frameHeartbeat>15000)showWaiting('服务暂未连接，每 5 秒自动检查一次。');
  }catch{if(!frameHeartbeat||Date.now()-frameHeartbeat>15000)showWaiting('暂时未收到连接状态，正在自动重试。');}
  finally{clearTimeout(timeout);checking=false}
}
document.getElementById('retry').onclick=()=>check(true);document.getElementById('reconnect').onclick=()=>check(true);
window.addEventListener('online',()=>check());document.addEventListener('visibilitychange',()=>{if(!document.hidden)check()});
check();setInterval(check,5000);
</script></body></html>`;
const icon = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="16" fill="#2c7252"/><text x="32" y="46" text-anchor="middle" font-size="44" fill="white">✿</text></svg>';
export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (path === "/favicon.svg") return new Response(icon,{headers:{...headers,"Content-Type":"image/svg+xml"}});
    try {
      if (path === "/api/update") {
        if (request.method !== "POST") return new Response("Method not allowed",{status:405,headers});
        if (!env.LINK_SYNC_TOKEN || request.headers.get("Authorization") !== "Bearer " + env.LINK_SYNC_TOKEN) return new Response("Unauthorized",{status:401,headers});
        if (Number(request.headers.get("Content-Length")) > 8192) return new Response("Too large",{status:413,headers});
        const raw = await request.text();
        if (raw.length > 8192) return new Response("Too large",{status:413,headers});
        let data; try {data=JSON.parse(raw);} catch {return new Response("Invalid JSON",{status:400,headers});}
        const url = data.url ? cleanUrl(data.url) : "";
        if ((data.url && !url) || !states.has(data.state) || typeof data.source_age !== "number" || !Number.isFinite(data.source_age) || data.source_age < 0) return new Response("Invalid status",{status:400,headers});
        await database(env).prepare("INSERT INTO latest_link (id,url,state,source_age,received_at) VALUES (1,?,?,?,?) ON CONFLICT(id) DO UPDATE SET url=excluded.url,state=excluded.state,source_age=excluded.source_age,received_at=excluded.received_at").bind(url,data.state,data.source_age,Date.now()).run();
        return Response.json({ok:true},{headers});
      }
      if (!["/","/api/latest","/latest.txt"].includes(path)) return new Response("Not found",{status:404,headers});
      if (!["GET","HEAD"].includes(request.method)) return new Response("Method not allowed",{status:405,headers});
      const row = await database(env).prepare("SELECT url,state,source_age,received_at FROM latest_link WHERE id=1").first();
      const latest = publicState(row);
      if (path === "/api/latest") return Response.json(latest,{headers});
      if (path === "/latest.txt") return new Response(latest.ready ? latest.url+"/\n" : "画图服务正在恢复，请稍后重新打开固定入口。\n",{headers:{...headers,"Content-Type":"text/plain; charset=utf-8"}});
      return new Response(request.method === "HEAD" ? null : shellPage,{headers:{...headers,"Content-Type":"text/html; charset=utf-8"}});
    } catch (error) {
      console.error("Portal storage unavailable:", error?.message);
      if (path === "/") return new Response(shellPage,{headers:{...headers,"Content-Type":"text/html; charset=utf-8"}});
      return Response.json({error:"服务暂不可用，请稍后重试"},{status:503,headers});
    }
  }
};
