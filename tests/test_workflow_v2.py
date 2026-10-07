"""Task isolation, 0–3 references, literal custom prompts and review termination (no desktop)."""
import io
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from PIL import Image
import chatgpt_image_ui as ui
from fabric_presets import FABRIC_CATALOG, make_fabric_prompt, prepare_prompt
from server import make_app


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.result = self.root/'result.png'
        Image.new('RGB', (32, 32), 'red').save(self.result)
        self.calls = []
        def runner(prompt, cfg, path, log, refs=None, review=None):
            self.calls.append((prompt, refs))
            return self.result
        self.start(runner)

    def start(self, runner):
        if hasattr(self, 'manager'):
            self.manager.close(2)
        self.app = make_app(self.root/'config.json', runner=runner, validate_startup=False)
        self.client = self.app.test_client()
        self.manager = self.app.extensions['job_manager']

    def tearDown(self):
        self.manager.close(2)
        self.tmp.cleanup()

    def upload(self, index):
        data=io.BytesIO()
        Image.new('RGB', (30+index, 30), (index*30,0,0)).save(data,format='PNG')
        data.seek(0)
        value=f'{index:032x}'
        response=self.client.post('/uploads', data={'image':(data,'test.png'),'upload_id':value})
        self.assertIn(response.status_code,(200,201))
        return value

    def submit(self, prompt='画猫', tool='custom', ids=None, request_id=None):
        return self.client.post('/jobs',json={'prompt':prompt,'tool':tool,'upload_ids':ids or [],'request_id':request_id})

    def wait(self, job, state):
        for _ in range(300):
            data=self.client.get('/jobs/'+job).get_json()
            if data['status']==state:return data
            time.sleep(.01)
        self.fail(str(data))

    def test_custom_exact_characters_zero_to_three_images(self):
        ids=[self.upload(n) for n in range(1,4)]
        prompt='  人物摄影\n沿用参考图配色；无参考图时采用协调自然配色。\n  '
        for count in range(4):
            with self.subTest(count=count):
                job=self.submit(prompt,ids=ids[:count]).get_json()['id']
                state=self.wait(job,'done')
                self.assertEqual(state['prompt'],prompt)
                self.assertEqual(state['sent_prompt'],prompt)
                self.assertEqual(state['referenceImages'],ids[:count])
                sent,refs=self.calls[-1]
                self.assertEqual(sent,prompt)
                actual=() if refs is None else refs if isinstance(refs,tuple) else (refs,)
                self.assertEqual(len(actual),count)
                for n,path in enumerate(actual,1):
                    self.assertIn(job,str(path))
                    with Image.open(path) as img:self.assertEqual(img.size,(30+n,30))

    def test_fourth_image_duplicate_and_invalid_list_rejected(self):
        ids=[self.upload(n) for n in range(1,5)]
        for values in [ids,ids[:1]*2,'abc',[{}]]:
            response=self.client.post('/jobs',json={'prompt':'猫','upload_ids':values})
            self.assertEqual(response.status_code,400)
        self.assertEqual(self.calls,[])

    def test_required_and_optional_tools(self):
        for tool in FABRIC_CATALOG['tools']:
            response=self.submit(tool=tool['id'])
            self.assertEqual(response.status_code,400 if tool['requires_reference'] else 202,tool['id'])
        reference=self.upload(1)
        for tool in FABRIC_CATALOG['tools']:
            self.assertEqual(self.submit(tool=tool['id'],ids=[reference]).status_code,202)

    def test_conditionals_are_resolved_before_submission(self):
        prompt='沿用参考图配色；无参考图时采用协调自然配色。'
        value=prepare_prompt(prompt,'fabric',False)
        self.assertNotIn('沿用参考图配色',value)
        self.assertIn('采用协调自然配色',value)
        self.assertIn('直接开始图片生成',value)
        with_ref=prepare_prompt(prompt,'fabric',True)
        self.assertEqual(with_ref,'沿用参考图配色。')
        self.assertNotIn('参考图',make_fabric_prompt(has_reference=False))
        self.assertIn('参考图',make_fabric_prompt(has_reference=True))
        self.assertNotIn('根据参考图',prepare_prompt(make_fabric_prompt(tool='seamless'),'seamless',False))

    def test_end_requires_confirmation_releases_queue_no_download(self):
        started=threading.Event(); calls=[]; downloads=[]
        def runner(prompt,cfg,path,log,refs=None,review=None):
            calls.append(prompt)
            if prompt=='A':
                started.set()
                review(self.result,'60秒未识别，请确认')
                downloads.append(prompt)
            if prompt=='B':raise RuntimeError('模拟执行失败')
            return self.result
        self.start(runner)
        first=self.submit('A',request_id='a').get_json()['id']
        state=self.wait(first,'review')
        second=self.submit('B').get_json()['id']
        third=self.submit('C').get_json()['id']
        self.assertEqual(self.wait(second,'queued')['prompt'],'B')
        self.assertEqual(self.wait(third,'queued')['prompt'],'C')
        endpoint='/jobs/'+first+'/review'
        for confirmed in [None,False,'true',1]:
            self.assertEqual(self.client.post(endpoint,json={'action':'end','version':state['review_version'],'confirmed':confirmed}).status_code,400)
            self.assertEqual(self.wait(first,'review')['review_version'],state['review_version'])
        self.assertEqual(self.client.post(endpoint,json={'action':'end','version':0,'confirmed':True}).status_code,409)
        self.assertEqual(self.client.post(endpoint,json={'action':'end','version':state['review_version'],'confirmed':True}).status_code,202)
        ended=self.wait(first,'not_generated')
        self.assertIsNone(ended['error']);self.assertIsNone(ended['result'])
        self.assertEqual(self.client.get('/jobs/'+first+'/image').status_code,409)
        self.wait(second,'error');self.wait(third,'done')
        self.assertEqual(calls,['A','B','C']);self.assertEqual(downloads,[])
        self.assertEqual(self.submit('A',request_id='a').get_json()['id'],first)
        self.assertEqual(calls,['A','B','C'])

    def test_immutable_copies_and_idempotency_all_image_ids_and_tool(self):
        blocked=threading.Event(); release=threading.Event(); seen=[]
        def runner(prompt,cfg,path,log,refs=None,review=None):
            if prompt=='block':blocked.set();release.wait(2)
            else:
                for ref in refs:
                    with Image.open(ref) as img:seen.append(img.size)
            return self.result
        self.start(runner)
        try:
            first=self.submit('block').get_json()['id'];self.assertTrue(blocked.wait(1))
            ids=[self.upload(n) for n in range(1,4)]
            second=self.submit('second',ids=ids,request_id='same').get_json()['id']
            self.assertEqual(self.submit('second',ids=ids,request_id='same').get_json()['id'],second)
            for values,tool in [(ids[:2],'custom'),(ids,'fabric')]:
                self.assertEqual(self.submit('second',tool,values,'same').status_code,409)
            for value in ids:(self.root/'uploads'/(value+'.png')).unlink()
        finally:release.set()
        self.wait(first,'done');self.wait(second,'done')
        self.assertEqual(seen,[(31,30),(32,30),(33,30)])

    def test_static_frontend_and_three_confirmation_buttons(self):
        html=self.client.get('/').get_data(as_text=True)
        for text in ['multiple','referencePreviews','endGeneration','确认结束本次生成任务？','取消，继续当前任务','确认结束任务','taskList']:
            self.assertIn(text,html)
        with self.client.get('/static/workflow.js') as response:
            self.assertEqual(response.status_code,200)


class DesktopMultipleTests(unittest.TestCase):
    def test_all_references_pasted_before_prompt_and_single_send(self):
        from test_upload_support import DesktopOfflineTests, image_data
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);a=DesktopOfflineTests().setup_auto(root)
            for name in ['done_marker.png','download_menu.png']:(root/name).write_bytes(image_data())
            sequence=[];a.prepare=Mock();a.new_chat=Mock();a.sleep=Mock()
            a.paste_reference=Mock(side_effect=lambda ref:sequence.append(ref.name))
            a.clipboard.copy=Mock(side_effect=lambda prompt:sequence.append(prompt))
            a.send=Mock(side_effect=lambda **kw:sequence.append('send'))
            a.wait_done=Mock(return_value=Image.new('RGB',(800,600)))
            a.download_image=Mock(return_value=root/'result.png')
            a.run('原样提示词',image_paths=[root/'1.png',root/'2.png',root/'3.png'])
            self.assertEqual(sequence,['1.png','2.png','3.png','原样提示词','send'])
            a.send.assert_called_once_with(timeout=60)
            with self.assertRaises(ValueError):a.run('x',image_paths=[root/'1.png']*4)

    def test_end_exits_before_activation_download_and_further_detection(self):
        with tempfile.TemporaryDirectory() as folder:
            a=ui.Automation.__new__(ui.Automation);a.assert_target=Mock();a.pag=Mock();a.sleep=Mock();a.log=Mock()
            a.shot=Mock(return_value=Image.new('RGB',(32,32)));a.review=Mock(return_value='end');a.win=Mock()
            with self.assertRaises(ui.NotGenerated):a.request_download_review(Path(folder),'确认')
            a.win.activate.assert_not_called()


if __name__=='__main__':unittest.main()
