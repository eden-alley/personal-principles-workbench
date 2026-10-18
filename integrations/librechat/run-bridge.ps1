$ErrorActionPreference = 'Stop'
$project = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$secrets = Get-Content -LiteralPath (Join-Path $project 'artifacts/librechat-runtime/service-secrets.json') -Raw | ConvertFrom-Json
$env:LIBRECHAT_BRIDGE_TOKEN = $secrets.bridge
Set-Location -LiteralPath $project
& (Join-Path $project '.venv/Scripts/python.exe') -m integrations.librechat.bridge --host 127.0.0.1 --port 8767
exit $LASTEXITCODE
