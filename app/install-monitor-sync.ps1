$ErrorActionPreference='Stop'
$a=Join-Path $env:LOCALAPPDATA 'ChatGPT-Worker\image-automation'
$python=Join-Path $env:LOCALAPPDATA 'ChatGPT-Worker\Python\pythonw.exe'
if (-not (Test-Path -LiteralPath "$a\cloud_agent_config.json")) {throw 'Cloud configuration missing'}
$command='"'+$python+'" "'+$a+'\kuma_sync.py"'
New-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'ChatGPTKumaSync' -Value $command -PropertyType String -Force | Out-Null
Start-Process -FilePath $python -ArgumentList ('"'+$a+'\kuma_sync.py"') -WorkingDirectory $a -WindowStyle Hidden
Write-Host 'Kuma statistics sync started; login startup enabled.'
