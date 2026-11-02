param([string]$PythonExecutable = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$clientTarget = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'client-target.json') -Raw | ConvertFrom-Json
if ($clientTarget.version -ne 1 -or $clientTarget.client -notin @('codex', 'deepseek')) { throw 'Invalid client-target.json. Restore the file from your release branch.' }
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
# The release branch chooses the client, not inherited environment variables.
if ($clientTarget.client -eq 'deepseek') {
    Write-Host 'DeepSeek release; merging MCP and skill configuration (existing entries preserved, backup written).'
    & '.venv/Scripts/python.exe' tools/onboarding/dsh_integration.py install
    if ($LASTEXITCODE -ne 0) {
        throw 'DeepSeek Harness configuration did not complete. Run python tools/onboarding/dsh_integration.py status --json and follow docs/DSH.md.'
    } else {
        Write-Host 'DeepSeek Harness configuration merged. Restart DeepSeek Harness and reopen this folder as the workspace.'
    }
}
& '.venv/Scripts/python.exe' tools/study/product_tour.py status
if ($LASTEXITCODE -ne 0) { throw 'Setup files are ready, but the usage guide status needs repair. Preserve existing study records.' }
Write-Host 'Setup complete. Continue in the current AI chat: read docs/CHAT_ONBOARDING.md, finish connection/course selection, and actively teach the user how to use the system. Do not send the user to a CMD menu.'
