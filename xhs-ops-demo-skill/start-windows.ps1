param(
    [int]$Port = 8765,
    [switch]$NoOpen,
    [switch]$WithPlaywright
)

$ErrorActionPreference = "Stop"
$SkillRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $SkillRoot

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$pythonCandidates = @()
if ($env:PYTHON_BIN) { $pythonCandidates += $env:PYTHON_BIN }
$pythonCandidates += @(
    (Join-Path $env:USERPROFILE ".workbuddy\binaries\python\versions\3.13.12\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe")
)

$python = $null
foreach ($candidate in $pythonCandidates) {
    if ($candidate -and (Test-Path -LiteralPath $candidate)) {
        $python = $candidate
        break
    }
}

if (-not $python) {
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd) {
        $python = $cmd.Source
    }
}

if (-not $python) {
    throw "Python not found. Set PYTHON_BIN to a Python 3 executable."
}

$args = @(".\scripts\launch_local.py", "--port", "$Port")
if ($NoOpen) { $args += "--no-open" }
if ($WithPlaywright) { $args += "--with-playwright" }

Write-Host "Starting local open lead workbench on http://127.0.0.1:$Port/index.html"
& $python @args
