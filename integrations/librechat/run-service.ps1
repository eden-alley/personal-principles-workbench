param([Parameter(Mandatory=$true)][ValidateSet('mongo','bridge','gateway','chat')][string]$Service)
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$runtime = Join-Path $project 'artifacts/librechat-runtime'
switch ($Service) {
  'mongo' {
    Set-Location -LiteralPath $project
    & (Join-Path $project 'artifacts/mongodb-8.0.20/mongodb-win32-x86_64-windows-8.0.20/bin/mongod.exe') --bind_ip 127.0.0.1 --port 27018 --dbpath (Join-Path $runtime 'mongo-data') --logpath (Join-Path $runtime 'mongo.log') --logappend
  }
  'bridge' { & (Join-Path $PSScriptRoot 'run-bridge.ps1') }
  'gateway' { & (Join-Path $PSScriptRoot 'run-gateway.ps1') }
  'chat' {
    Set-Location -LiteralPath (Join-Path $project 'artifacts/librechat-upstream')
    $env:NODE_ENV = 'production'
    & node api/server/index.js
  }
}
exit $LASTEXITCODE
