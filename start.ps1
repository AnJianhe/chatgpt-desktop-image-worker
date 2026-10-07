param(
    [ValidateSet('ui','server','watchdog','sync')][string]$Mode = 'ui',
    [int]$Port = 8765,
    [string]$EntryOrigin = '',
    [string]$Cloudflared = ''
)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw '请先按 README 创建项目内的 .venv 并安装依赖。'
}
if (-not $Cloudflared) { $Cloudflared = Join-Path $projectRoot 'tools\cloudflared-windows-amd64.exe' }
Set-Location -LiteralPath $projectRoot
switch ($Mode) {
    'ui'       { $workerArgs = @((Join-Path $projectRoot 'app\chatgpt_image_ui.py')) }
    'server'   { $workerArgs = @((Join-Path $projectRoot 'app\server.py'),'--port',"$Port"); if ($EntryOrigin) { $workerArgs += @('--entry-origin',$EntryOrigin) } }
    'watchdog' { $workerArgs = @((Join-Path $projectRoot 'app\tunnel_watchdog.py'),'--executable',$Cloudflared,'--origin',"http://127.0.0.1:$Port") }
    'sync'     { $workerArgs = @((Join-Path $projectRoot 'app\link_sync.py')) }
}
& $python @workerArgs
exit $LASTEXITCODE
