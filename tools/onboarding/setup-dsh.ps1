# DeepSeek Harness (DSH) completion check shared by setup.ps1 and its tests.
#
# Dot-source this file (it only defines functions and has no side effects), then:
#
#   $code = Invoke-DshConfiguration -Root <project root> [-PythonExecutable <python.exe>]
#
# It runs the real tools/onboarding/dsh_integration.py merge (human readable) and
# then verifies the structural result with `status --json`. Completion requires
# BOTH: install exit code 0 AND configuration_ok = true, which covers the
# moodle_study MCP entry and this project's skills directory. Returns 0 only on
# success. On success it prints the restart/login/runtime-verification reminder;
# on conflict/failure it prints actionable repair hints and never "Setup
# complete". The caller keeps ownership of the final setup message.
#
# Tests point DSH_HOME (and DSH_PROFILE_DIR when set) at a temporary profile, so
# no real DSH profile is read or written.

function Get-DshPythonPath {
    param([string]$Root, [string]$PythonExecutable)
    if ($PythonExecutable) {
        if (Test-Path -LiteralPath $PythonExecutable) {
            return (Resolve-Path -LiteralPath $PythonExecutable).Path
        }
        return ''
    }
    $candidate = Join-Path $Root '.venv/Scripts/python.exe'
    if (Test-Path -LiteralPath $candidate) {
        return (Resolve-Path -LiteralPath $candidate).Path
    }
    $found = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    $found = Get-Command python -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    return ''
}

function Read-DshStatusJson {
    param([string]$PythonPath, [string]$ScriptPath)
    $raw = & $PythonPath -B $ScriptPath status --json
    $exit = $LASTEXITCODE
    $text = (@($raw) | ForEach-Object { [string]$_ }) -join "`n"
    $data = $null
    if ($text.Trim()) {
        try { $data = $text | ConvertFrom-Json } catch { $data = $null }
    }
    return [pscustomobject]@{ ExitCode = $exit; Data = $data; Text = $text }
}

function Get-DshPartStatus {
    param($Data, [string]$Part)
    if ($null -eq $Data) { return 'unknown' }
    $node = $Data.PSObject.Properties | Where-Object { $_.Name -eq $Part } | Select-Object -First 1
    if ($null -eq $node -or $null -eq $node.Value) { return 'unknown' }
    $status = $node.Value.PSObject.Properties | Where-Object { $_.Name -eq 'status' } | Select-Object -First 1
    if ($null -eq $status -or $null -eq $status.Value) { return 'unknown' }
    return [string]$status.Value
}

function Get-DshValue {
    param($Data, [string[]]$Path)
    $current = $Data
    foreach ($name in $Path) {
        if ($null -eq $current) { return $null }
        $node = $current.PSObject.Properties | Where-Object { $_.Name -eq $name } | Select-Object -First 1
        if ($null -eq $node) { return $null }
        $current = $node.Value
    }
    return $current
}

function Invoke-DshConfiguration {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [string]$PythonExecutable = ''
    )
    $rootPath = (Resolve-Path -LiteralPath $Root).Path
    $python = Get-DshPythonPath -Root $rootPath -PythonExecutable $PythonExecutable
    if (-not $python) {
        Write-Host 'DSH configuration is NOT complete: the project Python environment was not found (.venv/Scripts/python.exe).'
        Write-Host 'Action required: finish the local install first; do not delete the existing DSH configuration.'
        return 1
    }
    $integration = Join-Path $rootPath 'tools/onboarding/dsh_integration.py'
    if (-not (Test-Path -LiteralPath $integration)) {
        Write-Host 'DSH configuration is NOT complete: tools/onboarding/dsh_integration.py is missing.'
        Write-Host 'Action required: restore the complete project files; do not delete the existing DSH configuration.'
        return 1
    }
    Write-Host 'Merging DeepSeek Harness configuration (existing entries preserved; backup written when the profile changes).'
    # Pipe through Write-Host: uncaptured native output would otherwise become
    # part of this function's return value and corrupt the exit code.
    & $python -B $integration install | Write-Host
    $installExit = $LASTEXITCODE
    $status = Read-DshStatusJson -PythonPath $python -ScriptPath $integration
    $configurationOk = $false
    if ($null -ne $status.Data) {
        $node = $status.Data.PSObject.Properties | Where-Object { $_.Name -eq 'configuration_ok' } | Select-Object -First 1
        if ($null -ne $node) { $configurationOk = [bool]$node.Value }
    }
    $mcpStatus = Get-DshPartStatus -Data $status.Data -Part 'mcp'
    $skillsStatus = Get-DshPartStatus -Data $status.Data -Part 'skills'
    if ($installExit -eq 0 -and $configurationOk) {
        Write-Host ('DeepSeek Harness configuration verified for both parts: MCP=' + $mcpStatus + ', skills=' + $skillsStatus + '.')
        Write-Host 'Next: fully restart DeepSeek Harness and reopen this project folder as the workspace.'
        Write-Host 'Then finish school login and chat course selection from the project chat; the in-chat assistant completes the login, so do not send the student to a CMD menu. MCP tools become usable only after that login.'
        Write-Host 'Final check: the configuration is correct, but runtime loading is not verified. Confirm the self-study-tutor skill and the moodle_study MCP tools in the restarted project chat.'
        return 0
    }
    Write-Host ('DSH configuration is NOT complete (install exit code ' + $installExit + '; configuration_ok=' + $configurationOk.ToString().ToLower() + '; MCP=' + $mcpStatus + '; skills=' + $skillsStatus + ').')
    $mcpEntryCount = Get-DshValue -Data $status.Data -Path @('mcp', 'entry_count')
    if ($skillsStatus -eq 'invalid') {
        Write-Host 'Skills invalid: this project skills directory does not exist, so the skill configuration cannot load. Restore the complete project files (the skills/ directory) and rerun setup.'
    }
    if ($mcpStatus -ne 'conflict' -and $skillsStatus -ne 'conflict' -and $skillsStatus -ne 'invalid') {
        Write-Host 'Read the merge output above: the profile patch may be unparsable or the profile directory may be missing.'
        Write-Host 'Action required: repair the DSH profile or merge manually per docs/DSH.md, then rerun setup.'
    }
    if ($mcpStatus -eq 'conflict') {
        if ($null -ne $mcpEntryCount -and [int]$mcpEntryCount -gt 1) {
            Write-Host ('MCP conflict: ' + [int]$mcpEntryCount + ' entries share serverName moodle_study, so the installer will not guess and --update-existing cannot choose one. Remove or merge the duplicates by hand per docs/DSH.md (keep only the entry that points at this project), then rerun setup.')
        } else {
            Write-Host 'MCP conflict: an existing moodle_study MCP entry does not point at this project. If it is an older deployment, run tools/onboarding/dsh_integration.py install --update-existing to update only that entry; otherwise merge manually per docs/DSH.md.'
        }
    }
    if ($skillsStatus -eq 'conflict') {
        Write-Host 'Skills conflict: an existing skill-filesystem override does not include this project skills directory. Merge or remove the old override per docs/DSH.md so the current skills directory is included.'
    }
    Write-Host 'Action required: fix the conflict or error above, then rerun setup. Do not delete the existing configuration and retry; nothing was overwritten.'
    return $(if ($installExit -ne 0) { $installExit } else { 1 })
}
