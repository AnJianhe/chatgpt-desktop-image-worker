"""Persistent records and image retrieval; no live desktop operations."""
from pathlib import Path
import sys
import tempfile
import time
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from PIL import Image
from server import make_app
from generation_history import GenerationHistory
from chatgpt_image_ui import NotGenerated


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.image=self.root/'original.png';Image.new('RGB',(18,12),'green').save(self.image)
        self.expected=self.image.read_bytes();self.calls=[];self.apps=[]

    def tearDown(self):
        for app in self.apps:app.extensions['job_manager'].close(2)
        self.temp.cleanup()

    def app(self, **kwargs):
        def run(prompt,*args):
            self.calls.append(prompt)
            if prompt=='失败':raise RuntimeError('模拟失败')
            if prompt=='未生成':raise NotGenerated('用户确认没有图片')
            return self.image
        app=make_app(self.root/'config.json',runner=run,validate_startup=False,**kwargs)
        self.apps.append(app);return app

    def job(self,client,prompt,request_id=None):
        response=client.post('/jobs',json={'prompt':prompt,'tool':'custom','request_id':request_id})
        self.assertIn(response.status_code,(200,202));job_id=response.json['id']
        for _ in range(300):
            state=client.get('/jobs/'+job_id).json
            if state['status'] in {'done','error','not_generated'}:return job_id
            time.sleep(.01)
        self.fail(str(state))

    def test_restart_keeps_record_prompt_and_original_image(self):
        app=self.app();client=app.test_client();job=self.job(client,'  原始提示词\n换行  ','once')
        app.extensions['job_manager'].close(2);self.image.unlink()
        second=self.app().test_client();page=second.get('/history').json
        self.assertEqual(page['total'],1)
        record=page['items'][0];self.assertEqual(record['prompt'],'  原始提示词\n换行  ')
        self.assertEqual(record['status'],'done');self.assertGreater(record['created_at'],0)
        self.assertFalse(any(k.startswith('_') for k in record))
        self.assertNotIn('request_id',record)
        with second.get(record['image_url']) as response:self.assertEqual(response.data,self.expected)
        self.assertEqual(second.get('/jobs/'+job).json['status'],'done')
        self.assertEqual(self.job(second,'  原始提示词\n换行  ','once'),job)
        self.assertEqual(len(self.calls),1)

    def test_filter_pagination_and_failure_distinction(self):
        client=self.app().test_client()
        for prompt in ['第一张','未生成','失败','第二张']:self.job(client,prompt)
        response=client.get('/history?limit=2&offset=0').json
        self.assertEqual(response['total'],4);self.assertEqual(len(response['items']),2)
        self.assertEqual(response['items'][0]['prompt'],'第二张')
        older=client.get('/history?limit=2&offset=2').json['items']
        self.assertEqual(len(older),2);self.assertNotEqual(response['items'][0]['id'],older[0]['id'])
        for status,count in [('done',2),('error',1),('not_generated',1)]:
            data=client.get('/history?status='+status).json
            self.assertEqual(data['total'],count)
            self.assertTrue(all(item['status']==status for item in data['items']))
        not_generated=client.get('/history?status=not_generated').json['items'][0]
        self.assertIsNone(not_generated['error']);self.assertIsNone(not_generated['result'])
        self.assertEqual(client.get('/history?status=invalid').status_code,400)
        self.assertEqual(client.get('/history?offset=bad').status_code,400)

    def test_memory_pruning_keeps_persistent_records(self):
        app=self.app(max_queue=1,max_history=2);client=app.test_client()
        first=self.job(client,'第一张','first')
        self.job(client,'第二张');self.job(client,'第三张')
        self.assertNotIn(first,app.extensions['job_manager'].jobs)
        self.assertEqual(client.get('/history').json['total'],3)
        self.assertEqual(client.get('/jobs/'+first).json['status'],'done')
        self.assertEqual(self.job(client,'第一张','first'),first)
        self.assertEqual(self.calls,['第一张','第二张','第三张'])

    def test_interrupted_tasks_are_recorded_but_never_replayed(self):
        store=GenerationHistory(self.root/'generation-history')
        for n,status in enumerate(['queued','running','review']):
            store.save({'id':str(n),'created_at':time.time(),'status':status,'prompt':'原提示词','sent_prompt':'原提示词','tool':'custom','referenceImages':[],'result':None,'error':None})
        client=self.app().test_client()
        items=client.get('/history').json['items']
        self.assertEqual(len(items),3);self.assertTrue(all(r['status']=='error' for r in items))
        self.assertTrue(all('重启' in r['error'] for r in items));self.assertEqual(self.calls,[])


if __name__=='__main__':unittest.main()
