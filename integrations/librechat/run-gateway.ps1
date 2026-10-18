$ErrorActionPreference = 'Stop'
$project = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$secrets = Get-Content -LiteralPath (Join-Path $project 'artifacts/librechat-runtime/service-secrets.json') -Raw | ConvertFrom-Json
$env:LIBRECHAT_BRIDGE_TOKEN = $secrets.bridge
$env:WORKBENCH_DATA_DIR = Join-Path $project 'data/librechat-library'
Set-Location -LiteralPath $project
& (Join-Path $project '.venv/Scripts/python.exe') -m integrations.librechat.gateway --port 8768
exit $LASTEXITCODE
