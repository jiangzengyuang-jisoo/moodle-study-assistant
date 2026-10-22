$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Install Node.js 22+ LTS from https://nodejs.org/ then reopen setup.cmd.' }
& node -e "if(Number(process.versions.node.split('.')[0])<22)process.exit(1)"
if ($LASTEXITCODE -ne 0) { throw 'Node.js 22 or newer is required.' }
if (Get-Command py -ErrorAction SilentlyContinue) { $pythonCommand = 'py'; $pythonArgs = @('-3') }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $pythonCommand = 'python'; $pythonArgs = @() }
else { throw 'Install Python 3.11+ from https://www.python.org/downloads/windows/ and enable Add Python to PATH.' }
& $pythonCommand @pythonArgs -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or newer is required.' }
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    & $pythonCommand @pythonArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the local Python environment.' }
}
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
Write-Host 'Setup complete. Next: connect.cmd, choose-courses.cmd, refresh.cmd. Read README.md.'
