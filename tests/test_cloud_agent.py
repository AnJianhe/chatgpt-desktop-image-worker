import importlib.util
from pathlib import Path
import tempfile
import unittest
import threading
from unittest.mock import Mock
from unittest.mock import patch
APP=Path(__file__).parents[1]/'app'
spec=importlib.util.spec_from_file_location('cloud_agent',APP/'cloud_agent.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class CloudAgentTests(unittest.TestCase):
 def test_independent_heartbeat_does_not_use_task_transfer_clients(self):
  with tempfile.TemporaryDirectory() as tmp:
   a=module.Agent({'endpoint':'https://example.invalid','worker_token':'test'},Path(tmp)/'s.json')
   stop=threading.Event();cloud=Mock();local=Mock();local.call.return_value={'worker_alive':True}
   cloud.call.side_effect=lambda *args,**kw:stop.set()
   a.cloud.call=Mock(side_effect=AssertionError('task client must not send background heartbeat'))
   a.local.call=Mock(side_effect=AssertionError('task client must not read background health'))
   with patch.object(module,'HTTP',side_effect=[cloud,local]) as factory,patch.object(module,'kuma_status',return_value={'monitors':[]}):
    a.heartbeat_loop(stop)
   self.assertEqual(cloud.call.call_args.args[0],'/worker/heartbeat')
   self.assertTrue(cloud.call.call_args.args[1]['local_health']['worker_alive'])
   self.assertEqual(factory.call_args_list[1].kwargs['timeout'],3)
 def test_three_images_and_restart_does_not_resubmit(self):
  with tempfile.TemporaryDirectory() as tmp:
   a=module.Agent({'endpoint':'https://example.invalid','worker_token':'test'},Path(tmp)/'state.json')
   calls=[];task={'id':'a'*32,'prompt':'  原样提示词  ','tool':'custom','upload_ids':['1','2','3'],'local_id':None}
   def cloud(path,data=None,method=None,raw=False,content_type=None):
    calls.append(('cloud',path,data))
    if path=='/worker/claim':return {'task':task}
    if '/worker/uploads/' in path:return (b'image','image/png')
    if path.endswith('/decision'):return {'decision':None}
    return {'ok':True}
   count=0
   def local(path,data=None,method=None,raw=False,content_type=None):
    nonlocal count
    calls.append(('local',path,data))
    if raw:return b'log','application/x-ndjson'
    if path=='/health':return {'worker_alive':True}
    if path=='/uploads':count+=1;return {'upload_id':str(count)}
    if path=='/jobs':return {'id':'localA'}
    return {'status':'running','stage':'生成中'}
   a.cloud.call=cloud;a.local.call=local
   with patch.object(module,'kuma_status',return_value={'monitors':[]}):a.step()
   submit=[x for x in calls if x[:2]==('local','/jobs')];self.assertEqual(len(submit),1)
   self.assertEqual(submit[0][2]['upload_ids'],['1','2','3']);self.assertEqual(submit[0][2]['prompt'],'  原样提示词  ')
   b=module.Agent({'endpoint':'https://example.invalid','worker_token':'test'},Path(tmp)/'state.json');b.cloud.call=cloud;b.local.call=local
   with patch.object(module,'kuma_status',return_value={'monitors':[]}):b.step()
   self.assertEqual(len([x for x in calls if x[:2]==('local','/jobs')]),1)

 def test_review_and_automatic_completion_without_user_decision(self):
  with tempfile.TemporaryDirectory() as tmp:
   a=module.Agent({'endpoint':'https://example.invalid','worker_token':'test'},Path(tmp)/'s.json');a.state={'task_id':'a','local_id':'localA'}
   calls=[];status='review'
   def cloud(path,data=None,method=None,**kw):
    calls.append((path,data,method));return {'decision':None,'ok':True}
   def local(path,**kw):
    if kw.get('raw'):return b'image','image/png'
    return {'status':status,'review_version':3}
   a.cloud.call=cloud;a.local.call=local
   a.sync('a','localA');self.assertTrue(any('/preview?v=3' in c[0] for c in calls))
   status='done';a.sync('a','localA');self.assertTrue(any(c[0].endswith('/result') for c in calls));self.assertEqual(a.state,{})

 def test_not_generated_never_downloads(self):
  with tempfile.TemporaryDirectory() as tmp:
   a=module.Agent({'endpoint':'https://example.invalid','worker_token':'test'},Path(tmp)/'s.json');a.state={'task_id':'a'}
   calls=[]
   a.cloud.call=lambda path,*args,**kw: {'decision':None}
   def local(path,**kw):
    calls.append(path)
    return (b'log','application/x-ndjson') if kw.get('raw') else {'status':'not_generated'}
   a.local.call=local;a.sync('a','localA');self.assertEqual(calls,['/jobs/localA','/jobs/localA/log']);self.assertEqual(a.state,{})

if __name__=='__main__':unittest.main()
