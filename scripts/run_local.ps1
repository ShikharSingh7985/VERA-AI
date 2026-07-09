$ErrorActionPreference = "Stop"
$env:PYTHONPATH = (Get-Location).Path
uvicorn bot:app --host 0.0.0.0 --port 8080

