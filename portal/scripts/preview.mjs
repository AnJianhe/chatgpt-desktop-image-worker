import http from 'node:http';
import worker from '../worker/index.js';
let row=null;
const env={LINK_SYNC_TOKEN:'local-preview',DB:{prepare(){return {bind(...v){this.v=v;return this},async run(){const [url,state,source_age,received_at]=this.v;row={url,state,source_age,received_at}},async first(){return row}}}}};
http.createServer(async(req,res)=>{try{let body='';for await(const part of req)body+=part;const r=await worker.fetch(new Request('http://127.0.0.1:8791'+req.url,{method:req.method,headers:req.headers,...(!['GET','HEAD'].includes(req.method)?{body}:{})}),env);res.writeHead(r.status,Object.fromEntries(r.headers));res.end(Buffer.from(await r.arrayBuffer()));}catch(e){res.writeHead(500);res.end(e.message)}}).listen(8791,'127.0.0.1',()=>console.log('Ready: http://127.0.0.1:8791/'));
