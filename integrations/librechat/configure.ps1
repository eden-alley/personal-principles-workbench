$ErrorActionPreference = 'Stop'
$project = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$upstream = Join-Path $project 'artifacts/librechat-upstream'
$runtime = Join-Path $project 'artifacts/librechat-runtime'
if (!(Test-Path -LiteralPath (Join-Path $upstream 'package.json'))) { throw '先准备固定版本 LibreChat v0.8.8 源码。' }
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
$secretFile = Join-Path $runtime 'service-secrets.json'
function New-HexSecret([int]$length) {
  $bytes = New-Object byte[] $length
  $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
  try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
  return ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
}
if (!(Test-Path -LiteralPath $secretFile)) {
  @{ bridge = (New-HexSecret 32); jwt = (New-HexSecret 32); refresh = (New-HexSecret 32); creds = (New-HexSecret 32); iv = (New-HexSecret 16) } |
    ConvertTo-Json | Set-Content -LiteralPath $secretFile -Encoding utf8
}
$secrets = Get-Content -LiteralPath $secretFile -Raw | ConvertFrom-Json
$provider = $env:LIBRARY_PROVIDER
if (!$provider -and (Test-Path -LiteralPath (Join-Path $project '.env'))) {
  foreach ($line in (Get-Content -LiteralPath (Join-Path $project '.env'))) {
    if ($line -match '^\s*LIBRARY_PROVIDER\s*=\s*(.*?)\s*$') {
      $provider = $Matches[1].Trim('"', "'")
      break
    }
  }
}
if (!$provider) { $provider = 'codex' }
if ($provider -notin @('codex','api')) { throw 'LIBRARY_PROVIDER must be codex or api.' }
$configName = if ($provider -eq 'api') { 'librechat-api.yaml' } else { 'librechat.yaml' }
$config = (Join-Path $PSScriptRoot $configName).Replace('\', '/')
$envFile = Join-Path $upstream '.env'
if ((Test-Path -LiteralPath $envFile) -and !(Test-Path -LiteralPath (Join-Path $runtime 'configured'))) {
  throw '已有非本脚本创建的 .env，停止以防覆盖。'
}
@"
HOST=127.0.0.1
PORT=3081
DOMAIN_CLIENT=http://127.0.0.1:3081
DOMAIN_SERVER=http://127.0.0.1:3081
MONGO_URI=mongodb://127.0.0.1:27018/LibreChatPilot
SEARCH=false
MEILI_NO_ANALYTICS=true
ENDPOINTS=custom
CONFIG_PATH=$config
LIBRECHAT_BRIDGE_TOKEN=$($secrets.bridge)
JWT_SECRET=$($secrets.jwt)
JWT_REFRESH_SECRET=$($secrets.refresh)
CREDS_KEY=$($secrets.creds)
CREDS_IV=$($secrets.iv)
ALLOW_EMAIL_LOGIN=true
ALLOW_REGISTRATION=true
ALLOW_SOCIAL_LOGIN=false
ALLOW_SOCIAL_REGISTRATION=false
ALLOW_PASSWORD_RESET=false
ALLOW_UNVERIFIED_EMAIL_LOGIN=true
ALLOW_SHARED_LINKS=false
ALLOW_SHARED_LINKS_PUBLIC=false
DEBUG_LOGGING=false
DEBUG_CONSOLE=false
"@ | Set-Content -LiteralPath $envFile -Encoding utf8
Set-Content -LiteralPath (Join-Path $runtime 'configured') -Value 'v0.8.8' -Encoding ascii
Write-Output '本地配置已准备；服务密钥保存在被 Git 忽略的独立运行目录。'
