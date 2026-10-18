$ErrorActionPreference = 'Stop'
$project = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$runtime = Join-Path $project 'artifacts/librechat-runtime'
if (!(Test-Path -LiteralPath (Join-Path $runtime 'configured'))) { & (Join-Path $PSScriptRoot 'configure.ps1') }
$runner = Join-Path $PSScriptRoot 'run-service.ps1'
$services = @(@{name='mongo';port=27018}, @{name='bridge';port=8767}, @{name='gateway';port=8768}, @{name='chat';port=3081})
foreach ($service in $services) {
  $listener = Get-NetTCPConnection -LocalPort $service.port -State Listen -ErrorAction SilentlyContinue
  if ($listener) {
    Write-Output "端口 $($service.port) 已监听，跳过启动 $($service.name)。"
    continue
  }
  $args = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $runner + '"'), '-Service', $service.name)
  $proc = Start-Process -FilePath 'powershell.exe' -ArgumentList $args -WorkingDirectory $project -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtime "$($service.name)-stdout.log") -RedirectStandardError (Join-Path $runtime "$($service.name)-stderr.log")
  Set-Content -LiteralPath (Join-Path $runtime "$($service.name)-launcher.pid") -Value $proc.Id -Encoding ascii
  $ready = $false
  for ($attempt=0; $attempt -lt 60; $attempt++) {
    if (Get-NetTCPConnection -LocalPort $service.port -State Listen -ErrorAction SilentlyContinue) { $ready=$true; break }
    if ($proc.HasExited) { throw "$($service.name) 启动退出，请查看 $runtime 下的日志。" }
    Start-Sleep -Milliseconds 500
    $proc.Refresh()
  }
  if (!$ready) { throw "$($service.name) 启动未就绪，请查看 $runtime 下的日志。" }
}
Write-Output 'LibreChat: http://127.0.0.1:3081'
