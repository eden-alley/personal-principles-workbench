param([string]$Python = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    if ($Python) {
        & $Python -m venv .venv
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -m venv .venv
    } else {
        & python -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12+ is required. Install Python, or pass -Python with its absolute path.' }
}
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check your network and try again.' }
& '.\.venv\Scripts\python.exe' manage.py migrate
if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
& '.\.venv\Scripts\python.exe' manage.py check
if ($LASTEXITCODE -ne 0) { throw 'Application checks failed.' }
Write-Host 'Ready. Run .\start.ps1, then open http://127.0.0.1:8765'
