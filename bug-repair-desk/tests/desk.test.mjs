import test from 'node:test';
import assert from 'node:assert/strict';
import worker,{page} from '../worker/index.js';
test('Chinese private repair desk supports maintainer classification and valid script',()=>{
 assert.match(page,/lang="zh-CN"/);assert.match(page,/ChatGPT/);assert.match(page,/用户无需判断/);
 new Function(page.match(/<script>([\s\S]*?)<\/script>/)[1]);
});
test('desk does not expose credentials and rejects cross-origin changes',async()=>{
 const r=await worker.fetch(new Request('https://desk.test/'),{BUG_ADMIN_TOKEN:'sensitive'});assert.doesNotMatch(await r.text(),/sensitive/);
 const denied=await worker.fetch(new Request('https://desk.test/api/reports/'+'a'.repeat(32),{method:'POST',headers:{Origin:'https://other.test'},body:'{}'}),{});assert.equal(denied.status,403);
 assert.equal((await worker.fetch(new Request('https://desk.test/api/reports'),{})).status,503);
});
