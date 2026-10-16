import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from bug_notifier import receive

class BugNotifierTests(unittest.TestCase):
 def test_persist_before_receipt_and_notify_once(self):
  with tempfile.TemporaryDirectory() as tmp:
   folder=Path(tmp);report={'id':'a'*32,'description':'有图片但提示没选图片','delivered':None}
   class Cloud:
    def call(self,path,data=None):
     if path=='/worker/bugs':return {'items':[report]}
     saved=json.loads((folder/(report['id']+'.json')).read_text(encoding='utf-8'))
     self.persisted=saved['description']==report['description']
   cloud=Cloud();notices=[];receive(cloud,folder,notices.append);receive(cloud,folder,notices.append)
   self.assertTrue(cloud.persisted);self.assertEqual(len(notices),1)
 def test_failed_receipt_can_retry_without_losing_report(self):
  with tempfile.TemporaryDirectory() as tmp:
   report={'id':'b'*32,'description':'测试','delivered':None};folder=Path(tmp)
   class Cloud:
    def call(self,path,data=None):
     if path=='/worker/bugs':return {'items':[report]}
     raise OSError('network')
   with self.assertRaises(OSError):receive(Cloud(),folder,lambda r:None)
   self.assertTrue((folder/('b'*32+'.json')).is_file())
