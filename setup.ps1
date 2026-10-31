param([string]$PythonExecutable = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
. (Join-Path $PSScriptRoot 'tools/onboarding/python-runtime.ps1')
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Install Node.js 22+ LTS from https://nodejs.org/ then reopen setup.cmd.' }
& node -e "if(Number(process.versions.node.split('.')[0])<22)process.exit(1)"
if ($LASTEXITCODE -ne 0) { throw 'Node.js 22 or newer is required.' }
$setupPython = Find-StudyPython -Root $PSScriptRoot -PreferredPython $PythonExecutable
if (-not $setupPython) { throw 'Install Python 3.11+ from https://www.python.org/downloads/windows/ and enable Add Python to PATH.' }
$pythonCommand = $setupPython.Command
$pythonArgs = $setupPython.Prefix
if ((Test-Path -LiteralPath '.venv/Scripts/python.exe') -and
    -not (Test-StudyPythonCommand -Command (Join-Path $PSScriptRoot '.venv/Scripts/python.exe'))) {
    throw 'The existing project Python is broken or too old. Repair .venv; preserve Study and configuration.'
}
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    & $pythonCommand @pythonArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the local Python environment.' }
}
try { Repair-StudyPip -Python (Join-Path $PSScriptRoot '.venv/Scripts/python.exe') }
catch { Write-Host $_.Exception.Message; exit 3 }
& '.venv/Scripts/python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
$npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npmCommand) { throw 'npm.cmd was not found. Reinstall the official Node.js LTS package.' }
& $npmCommand.Source ci --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { throw 'Node dependency installation failed.' }
& node tools/moodle/run.mjs init
if ($LASTEXITCODE -ne 0) { throw 'Initialization failed.' }
& '.venv/Scripts/python.exe' tools/study/review.py init
if ($LASTEXITCODE -ne 0) { throw 'Review initialization failed.' }
& '.venv/Scripts/python.exe' tools/study/daily.py run
if ($LASTEXITCODE -ne 0) { throw 'Learning workflow initialization failed.' }
& '.venv/Scripts/python.exe' tools/study/library.py sync
if ($LASTEXITCODE -ne 0) { throw 'Material library initialization failed. Your study records are preserved.' }
Write-Host 'Setup complete. Next: start.cmd. Read docs/START.md. Existing study records are preserved.'
