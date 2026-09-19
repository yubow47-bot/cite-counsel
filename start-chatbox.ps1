param([switch]$Restart)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$chatboxPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $chatboxPython)) {
    throw 'Run uv venv --python 3.11 .venv and uv pip install --python .venv/Scripts/python.exe -r requirements.txt first.'
}
$chatboxEntry = Join-Path $PSScriptRoot 'run_chatbox.py'
if ($Restart) {
    $chatboxConfig = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'config\chatbox.example.json') -Raw | ConvertFrom-Json
    $chatboxLocal = Join-Path $PSScriptRoot 'config\chatbox.local.json'
    if (Test-Path -LiteralPath $chatboxLocal) {
        $chatboxOverrides = Get-Content -LiteralPath $chatboxLocal -Raw | ConvertFrom-Json
        if ($null -ne $chatboxOverrides.port) { $chatboxConfig.port = $chatboxOverrides.port }
    }
    $chatboxListeners = Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort $chatboxConfig.port -State Listen -ErrorAction SilentlyContinue
    foreach ($chatboxListener in $chatboxListeners) {
        $chatboxOwner = Get-CimInstance Win32_Process -Filter "ProcessId = $($chatboxListener.OwningProcess)"
        if ($chatboxOwner.Name -ne 'python.exe' -or -not $chatboxOwner.CommandLine.Contains($chatboxEntry)) {
            throw 'Port belongs to a different process; refusing to stop it.'
        }
        Stop-Process -Id $chatboxOwner.ProcessId
        Wait-Process -Id $chatboxOwner.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
    }
}
& $chatboxPython $chatboxEntry
