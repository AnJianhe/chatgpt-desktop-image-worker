import http from 'node:http';
import {monitorPage} from '../worker/monitor-page.js';
const port=Number(process.env.PORT||8792);
const server=http.createServer(async(req,res)=>{
 try{
  if(req.url==='/api/monitor'){
   const data=process.env.MONITOR_ORIGIN ? await (await fetch(new URL('/api/monitor',process.env.MONITOR_ORIGIN))).json() : {ready:false,kuma_stale:true,kuma:{monitors:[],error:'本地预览尚未配置监控数据源'}};
   res.writeHead(200,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(data));
  }else{res.writeHead(200,{'Content-Type':'text/html; charset=utf-8'});res.end(monitorPage);}
 }catch{res.writeHead(502);res.end('监控数据暂不可读取');}
});
server.listen(port,'127.0.0.1',()=>console.log('Ready: http://127.0.0.1:'+port+'/monitor'));
process.on('SIGINT',()=>server.close());
