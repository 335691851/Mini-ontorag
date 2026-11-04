$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.venv\Scripts\python.exe')) { py -3.12 -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\ontorag.exe doctor
& .\.venv\Scripts\python.exe -c 'from ontorag_service.db import init_db; init_db()'
