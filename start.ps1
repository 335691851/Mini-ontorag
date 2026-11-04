$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw '未找到 Python 虚拟环境。请先运行 setup.ps1。' }
& $python -m uvicorn app:app --host 127.0.0.1 --port 8890
