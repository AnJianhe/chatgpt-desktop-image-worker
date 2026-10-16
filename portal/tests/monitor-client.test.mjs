import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {monitorPage} from '../worker/monitor-page.js';

test('monitor client: renders real values, curves, empty history and escapes monitor names',async()=>{
 const elements=new Map();
 const element=id=>{if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',classList:{toggle(){},remove(){}},querySelectorAll(){return []}});return elements.get(id)};
 const snapshot={ready:true,kuma_updated:Date.now(),kuma_stale:false,kuma:{monitors:[{id:1,name:'<img src=x onerror=alert(1)>',status:1,time:1700000000,ping:12,recent:[{time:1700000000,status:1,ping:12}],stats:{'24h':{uptime:99.5,avg_ping:14,samples:20}},series:{'24h':[{time:1700000000,ping:12},{time:1700000600,ping:16}]}}]}};
 const context=vm.createContext({document:{getElementById:element},fetch:async()=>({ok:true,json:async()=>snapshot}),setInterval(){}});
 vm.runInContext(monitorPage.match(/<script>([\s\S]*?)<\/script>/)[1],context);
 await new Promise(setImmediate);
 assert.match(element('detail').innerHTML,/99.5%/);assert.match(element('detail').innerHTML,/12 毫秒/);
 assert.match(element('detail').innerHTML,/<polyline/);assert.doesNotMatch(element('detail').innerHTML,/<img src=x/);
 assert.match(element('list').innerHTML,/&lt;img/);
 vm.runInContext("range='1y';render()",context);
 assert.match(element('detail').innerHTML,/暂无响应时间记录/);
 vm.runInContext('data.kuma_stale=true;render()',context);
 assert.match(element('detail').innerHTML,/数据过期/);
 assert.doesNotMatch(element('detail').innerHTML,/class="metric">12 毫秒/);
 vm.runInContext('data.kuma.monitors=[];render()',context);
 assert.match(element('detail').innerHTML,/尚未同步监控记录/);
});
