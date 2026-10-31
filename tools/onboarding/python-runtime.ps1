# Shared interpreter selection for bootstrap and setup. All probes are local.
function Test-StudyPythonCommand {
    param([string]$Command, [string[]]$Prefix = @(), [switch]$CheckPip)
    $studyProcess = $null
    try {
        $studyInfo = New-Object System.Diagnostics.ProcessStartInfo
        $studyInfo.FileName = $Command
        $studyProbeArgs = @('-B', '-c', '"import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)"')
        if ($CheckPip) { $studyProbeArgs = @('-B', '-m', 'pip', '--version') }
        $studyInfo.Arguments = (($Prefix + $studyProbeArgs) -join ' ')
        $studyInfo.UseShellExecute = $false
        $studyInfo.CreateNoWindow = $true
        $studyInfo.RedirectStandardOutput = $true
        $studyInfo.RedirectStandardError = $true
        $studyProcess = [System.Diagnostics.Process]::Start($studyInfo)
        # Drain both streams without disclosing launcher errors or local paths.
        $studyOut = $studyProcess.StandardOutput.ReadToEndAsync()
        $studyErr = $studyProcess.StandardError.ReadToEndAsync()
        if (-not $studyProcess.WaitForExit(10000)) {
            $studyProcess.Kill()
            $studyProcess.WaitForExit()
            return $false
        }
        return $studyProcess.ExitCode -eq 0
    } catch { return $false }
    finally { if ($studyProcess) { $studyProcess.Dispose() } }
}

function Find-StudyPython {
    param([string]$Root, [string]$PreferredPython = '')
    $studyCandidates = @()
    $studyLocalPython = Join-Path $Root '.venv/Scripts/python.exe'
    if (Test-Path -LiteralPath $studyLocalPython -PathType Leaf) {
        $studyCandidates += @{Command = $studyLocalPython; Prefix = @()}
    }
    if ($PreferredPython -and (Test-Path -LiteralPath $PreferredPython -PathType Leaf)) {
        $studyCandidates += @{Command = $PreferredPython; Prefix = @()}
    }
    foreach ($studyName in @('py', 'python')) {
        $studyCommand = Get-Command $studyName -CommandType Application -ErrorAction SilentlyContinue
        if ($studyCommand) {
            $studyPrefix = @()
            if ($studyName -eq 'py') { $studyPrefix = @('-3') }
            $studyCandidates += @{Command = $studyCommand.Source; Prefix = $studyPrefix}
        }
    }
    foreach ($studyCandidate in $studyCandidates) {
        if (Test-StudyPythonCommand -Command $studyCandidate.Command -Prefix $studyCandidate.Prefix) {
            return $studyCandidate
        }
    }
    return $null
}

function Repair-StudyPip {
    param([string]$Python)
    if (Test-StudyPythonCommand -Command $Python -CheckPip) { return }
    Write-Host '项目环境缺少可用的 pip，正在使用 Python 内置安装包修复；此修复不联网，不修改学习记录。'
    & $Python -B -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0 -or -not (Test-StudyPythonCommand -Command $Python -CheckPip)) {
        throw '内置 pip 修复未成功。请让助手修复 .venv 环境，保留 Study、授权和配置；不要删除学习档案或反复安装。'
    }
}
