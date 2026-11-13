param([string]$PythonExecutable = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$clientTarget = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'client-target.json') -Raw | ConvertFrom-Json
if ($clientTarget.version -ne 1 -or $clientTarget.client -notin @('codex', 'deepseek')) { throw 'Invalid client-target.json. Restore the file from your release branch.' }
. (Join-Path $PSScriptRoot 'tools/onboarding/python-runtime.ps1')
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Install Node.js 22+ LTS from https://nodejs.org/ then reopen setup.cmd.' }
& node -e "if(Number(process.versions.node.split('.')[0])<22)process.exit(1)"
if ($LASTEXITCODE -ne 0) { throw 'Node.js 22 or newer is required.' }
$stateFile = Join-Path $PSScriptRoot 'Study/_System/state.json'
$previousStateFile = Join-Path $PSScriptRoot 'Study/_System/state.previous.json'
if (-not (Test-Path -LiteralPath $stateFile) -and (Test-Path -LiteralPath $previousStateFile)) {
    throw 'The learning profile state.json is missing while state.previous.json still exists. Initialization was stopped so the last recoverable backup is not replaced with a blank profile. Back up Study/_System and restore the primary state before running setup again; do not delete the backup.'
}
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
    $dshHelper = Join-Path $PSScriptRoot 'tools/onboarding/setup-dsh.ps1'
    if (Test-Path -LiteralPath $dshHelper) {
        . $dshHelper
        $dshExit = Invoke-DshConfiguration -Root $PSScriptRoot
    } else {
        # A partial copy of the project may not ship the completion helper yet.
        # The install exit code alone is not enough: verify the persisted
        # configuration structurally with `status --json` and require
        # configuration_ok = true before reporting success.
        Write-Host 'DSH completion helper not found; verifying install and status --json.'
        & '.venv/Scripts/python.exe' tools/onboarding/dsh_integration.py install
        $dshExit = $LASTEXITCODE
        if ($dshExit -eq 0) {
            $statusRaw = & '.venv/Scripts/python.exe' -B tools/onboarding/dsh_integration.py status --json
            $statusText = (@($statusRaw) | ForEach-Object { [string]$_ }) -join "`n"
            $statusData = $null
            if ($statusText.Trim()) {
                try { $statusData = $statusText | ConvertFrom-Json } catch { $statusData = $null }
            }
            $configurationOk = $false
            if ($null -ne $statusData) {
                $okNode = $statusData.PSObject.Properties | Where-Object { $_.Name -eq 'configuration_ok' } | Select-Object -First 1
                if ($null -ne $okNode) { $configurationOk = [bool]$okNode.Value }
            }
            if (-not $configurationOk) {
                Write-Host 'DSH fallback verification failed: install exited 0 but `status --json` did not report configuration_ok=true.'
                Write-Host 'The DeepSeek Harness configuration (MCP + skills) is NOT verified; do not delete the existing configuration.'
                $dshExit = 1
            }
        }
    }
    if ($dshExit -ne 0) {
        Write-Host 'Setup did not complete: the DeepSeek Harness configuration (MCP + skills) is NOT verified.'
        Write-Host 'Do not delete the existing configuration; fix the conflict or error described above and rerun setup.'
        exit $dshExit
    }
}
& '.venv/Scripts/python.exe' tools/study/product_tour.py status
if ($LASTEXITCODE -ne 0) { throw 'Usage guide status needs repair. Preserve existing study records.' }
if ($clientTarget.client -eq 'deepseek') {
    Write-Host 'Setup complete for the local files and the merged DeepSeek Harness configuration (MCP + skills). Restart DeepSeek Harness and reopen this project folder; then finish school login and chat course selection from the project chat (the in-chat assistant completes the login; do not send the student to a CMD menu). A correct configuration is not runtime verification: confirm the self-study-tutor skill and the moodle_study MCP tools in the restarted project chat. Continue in this chat: follow docs/CHAT_ONBOARDING.md, then proactively teach the user how to use this system.'
} else {
    Write-Host 'Setup complete. Continue in this chat: follow docs/CHAT_ONBOARDING.md, finish school connection and chat course selection, then proactively teach the user how to use this system. Do not send the user to a CMD menu.'
}
