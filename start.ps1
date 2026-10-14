$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python manage.py demo
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host 'Open http://127.0.0.1:8000 in your browser. Press Ctrl+C to stop.'
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
