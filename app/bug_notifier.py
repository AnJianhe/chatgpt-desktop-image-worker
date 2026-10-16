"""Durable cloud Bug inbox and non-focus-stealing Windows tray notifications."""
import ctypes
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import subprocess
import time
from cloud_agent import HTTP

BASE = Path(__file__).resolve().parent


def receive(cloud, folder, notify):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for report in cloud.call('/worker/bugs')['items']:
        report_id = report.get('id','')
        if not re.fullmatch('[a-f0-9]{32}',report_id):
            continue
        path = folder / (report_id+'.json')
        try:
            previous = json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError):
            previous = {}
        notified = previous.get('_notified',False)
        saved = {**report,'_notified':notified}
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(saved,ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(tmp,path)
        if not report.get('delivered'):
            cloud.call('/worker/bugs/'+report_id+'/receipt',{})
        if not notified:
            notify(report)
            saved['_notified']=True
            tmp.write_text(json.dumps(saved,ensure_ascii=False,indent=2),encoding='utf-8')
            os.replace(tmp,path)
            logging.info('收到 Bug %s：%s',report_id,report['description'])


def balloon(report):
    if os.name != 'nt':
        return
    # User text is data in an environment variable, never interpolated PowerShell.
    script=r'''Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$data=$env:CHATGPT_BUG_NOTICE | ConvertFrom-Json
$icon=New-Object System.Windows.Forms.NotifyIcon
$icon.Icon=[System.Drawing.SystemIcons]::Information
$icon.Text='ChatGPT Worker Bug reports'
$icon.Visible=$true
$icon.ShowBalloonTip(6000,'收到新的 Bug 报告',[string]$data.text,[System.Windows.Forms.ToolTipIcon]::Warning)
Start-Sleep -Seconds 8
$icon.Dispose()'''
    env=dict(os.environ,CHATGPT_BUG_NOTICE=json.dumps({'text':report['description'][:180]+'\n请在 Bug 修复提交台查看。'},ensure_ascii=False))
    subprocess.Popen(['powershell.exe','-NoProfile','-Command',script],env=env,
                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


def main():
    if os.name=='nt':
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_bool,ctypes.c_wchar_p]
        kernel.CreateMutexW.restype=ctypes.c_void_p
        handle=kernel.CreateMutexW(None,False,'Local\\ChatGPTBugNotifier')
        if not handle:raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error()==183:return
    logs=BASE/'logs';logs.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s',handlers=[RotatingFileHandler(logs/'bug-notifier.log',maxBytes=1048576,backupCount=2,encoding='utf-8')])
    config=json.loads((BASE/'cloud_agent_config.json').read_text(encoding='utf-8-sig'))
    cloud=HTTP(config['endpoint'],config['worker_token'],timeout=10)
    while True:
        try:
            receive(cloud,logs/'bug-inbox',balloon)
        except Exception as exc:
            logging.warning('Bug报告同步暂时中断（%s），10秒后重试',type(exc).__name__)
        time.sleep(10)


if __name__=='__main__':main()
