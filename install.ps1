# PyTorchUI installer for Windows.
#
# Usage (PowerShell):
#   irm https://raw.githubusercontent.com/OpenNoorIlm/PyTorchUI/main/install.ps1 | iex
#
# Or locally:
#   powershell -ExecutionPolicy Bypass -File install.ps1

[CmdletBinding()]
param(
    [string]$Dir = "",
    [string]$Branch = "main",
    [switch]$NoVenv,
    [switch]$NoBuild,
    [switch]$Launch,
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$RepoUrl = if ($env:PYTORCHUI_REPO) { $env:PYTORCHUI_REPO } else {
    "https://github.com/OpenNoorIlm/PyTorchUI.git"
}
if (-not $Dir) {
    $Dir = Join-Path $env:USERPROFILE "PyTorchUI"
}

function Write-Step($m) { if (-not $Quiet) { Write-Host "==> $m" -ForegroundColor Cyan } }
function Write-Info($m) { if (-not $Quiet) { Write-Host "    $m" } }
function Write-Ok($m)   { if (-not $Quiet) { Write-Host "    * $m" -ForegroundColor Green } }
function Write-Warn($m) { Write-Host "    ! $m" -ForegroundColor Yellow }
function Write-Fail($m) { Write-Host "    x $m" -ForegroundColor Red; exit 1 }

if ($PSVersionTable.PSEdition -eq "Core") {
    $isWin = $IsWindows
} else {
    $isWin = $true
}
if (-not $isWin) {
    Write-Warn "This script is for Windows.  Use install.sh on Linux/macOS."
    exit 1
}

function Test-Python($exe) {
    try {
        $out = & $exe -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        $p = $out.Split(".") | ForEach-Object { [int]$_ }
        if ($p[0] -lt 3) { return $null }
        if ($p[0] -eq 3 -and $p[1] -lt 9) { return $null }
        return $out
    } catch { return $null }
}

Write-Step "Looking for Python 3.9+"
$PythonExe = $null
$PythonVer = $null

$py = Get-Command "py" -ErrorAction SilentlyContinue
if ($py) {
    try {
        $out = & py -3 -c "import sys; print(sys.executable)"
        if ($LASTEXITCODE -eq 0) {
            $cand = $out.Trim()
            $v = Test-Python $cand
            if ($v) { $PythonExe = $cand; $PythonVer = $v }
        }
    } catch { }
}

if (-not $PythonExe) {
    foreach ($cmd in @("python", "python3")) {
        $c = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($c) {
            $v = Test-Python $c.Source
            if ($v) { $PythonExe = $c.Source; $PythonVer = $v; break }
        }
    }
}

if (-not $PythonExe) {
    Write-Fail "No Python 3.9 or newer found.  Install with winget:
    winget install Python.Python.3.12
    Or download:  https://www.python.org/downloads/windows/"
}
Write-Info "Found: $PythonExe  ($PythonVer)"

Write-Step "Checking for git"
if (-not (Get-Command "git" -ErrorAction SilentlyContinue)) {
    Write-Fail "git is not installed.  Install with winget:
    winget install Git.Git
    Or download:  https://git-scm.com/download/win"
}
Write-Info "git: $(& git --version)"

Write-Step "Install directory: $Dir"

$existing = $false
if (Test-Path $Dir) {
    if (Test-Path (Join-Path $Dir ".git")) {
        $existing = $true
        Write-Info "Existing checkout found.  Will update in place."
    } else {
        $c = Get-ChildItem -Path $Dir -Force -ErrorAction SilentlyContinue
        if ($c.Count -eq 0) {
            Write-Info "Directory exists and is empty."
        } else {
            Write-Fail "Directory exists and is not empty:
    $Dir
    Move it aside, or pass -Dir <path>."
        }
    }
}

if ($existing) {
    Write-Step "Updating existing checkout"
    Push-Location $Dir
    try {
        & git fetch origin $Branch
        if ($LASTEXITCODE -ne 0) { throw "git fetch failed" }
        & git checkout $Branch
        if ($LASTEXITCODE -ne 0) { throw "git checkout failed" }
        & git pull --ff-only origin $Branch
        if ($LASTEXITCODE -ne 0) { throw "git pull failed" }
        Write-Ok "Updated $Dir"
    } finally { Pop-Location }
} else {
    Write-Step "Cloning $RepoUrl"
    & git clone --branch $Branch --depth 1 $RepoUrl $Dir
    if ($LASTEXITCODE -ne 0) { Write-Fail "git clone failed" }
    Write-Ok "Cloned to $Dir"
}

Set-Location $Dir

$PyExec = $PythonExe

if (-not $NoVenv) {
    Write-Step "Creating virtual environment"
    if (-not (Test-Path ".venv")) {
        & $PythonExe -m venv .venv
        if ($LASTEXITCODE -ne 0) { Write-Fail "venv creation failed" }
        Write-Ok "Created .venv"
    } else {
        Write-Info ".venv already exists"
    }
    $PyExec = Join-Path $Dir ".venv\Scripts\python.exe"
    if (-not (Test-Path $PyExec)) {
        Write-Fail "venv python not found at $PyExec"
    }
    Write-Info "Using $PyExec"
}

[System.IO.File]::WriteAllText(
    (Join-Path $Dir ".pytorchui_python"), $PyExec)

Write-Step "Installing dependencies"
& $PyExec -m pip install --upgrade pip 2>$null | Out-Null

if (Test-Path "requirements.txt") {
    & $PyExec -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { Write-Fail "pip install failed" }
    Write-Ok "Requirements installed"
} else {
    Write-Warn "No requirements.txt, installing the essentials directly"
    & $PyExec -m pip install PyQt5 matplotlib
    if ($LASTEXITCODE -ne 0) { Write-Fail "pip install failed" }
}

$null = & $PyExec -c "import PyQt5" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Fail "PyQt5 is not importable in the chosen Python.
    Try:  $PyExec -m pip install PyQt5"
}
Write-Ok "PyQt5 imports OK"

if (-not $NoBuild) {
    Write-Step "Building the node database"
    Write-Info "This walks every installed library and takes a few minutes."
    if (Test-Path "build.py") {
        & $PyExec build.py -q
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "build.py exited non-zero — retry with: python build.py"
        }
    } elseif (Test-Path "create.py") {
        & $PyExec create.py --db --quiet
    } else {
        Write-Warn "Neither build.py nor create.py found"
    }
    if ((Test-Path "main.db") -or (Test-Path "data\main.db")) {
        Write-Ok "Database built"
    } else {
        Write-Warn "No database file found — the editor starts empty."
    }
}

Write-Host ""
Write-Host "Installed" -ForegroundColor Green
Write-Host ""
Write-Host "  Directory:    $Dir"
Write-Host "  Interpreter:  $PyExec"
Write-Host ""
Write-Host "  To launch:"
Write-Host "      cd $Dir"
Write-Host "      .\start.bat"
Write-Host ""

if ($Launch) {
    Write-Step "Launching the editor"
    & $PyExec main.py
}
