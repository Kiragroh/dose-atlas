$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
Write-Host 'Dose Atlas: http://127.0.0.1:18766 (research only; Ctrl+C to stop)'
Write-Host 'Without model weights, only the clearly labelled synthetic analytic demo is available.'
python -m uvicorn app:app --host 127.0.0.1 --port 18766 --no-access-log
