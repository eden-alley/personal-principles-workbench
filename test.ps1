$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
# Tests never call a paid provider. Remote protocol tests use a fake transport.
$env:AI_API_KEY = ''
$env:AI_PROVIDER = 'demo'
& '.\.venv\Scripts\python.exe' manage.py test --verbosity 2
exit $LASTEXITCODE
