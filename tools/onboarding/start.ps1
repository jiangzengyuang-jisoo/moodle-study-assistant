param(
    [ValidateSet('Guide', 'Diagnose')][string]$Mode = 'Guide',
    [switch]$Online,
    [switch]$Json,
    [switch]$NoPause
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$entryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location -LiteralPath $entryRoot

# Setup uses this same resolver; interpreter selection must not diverge.
. (Join-Path $PSScriptRoot 'python-runtime.ps1')
$entryPython = Find-StudyPython -Root $entryRoot
if (-not $entryPython) {
    $entryMessage = '未找到可用的 Python 3.11 或更新版本。请从 https://www.python.org/downloads/windows/ 安装并勾选 Add Python to PATH，再重新打开 start.cmd。'
    if ($Json) {
        @{schema_version=1; mode='bootstrap'; checks=@(@{id='python'; status='error'; message=$entryMessage; next_step='安装 Python 后重新打开入口。'})} | ConvertTo-Json -Depth 4
    } else {
        Write-Host $entryMessage
        Write-Host '此步骤没有安装任何软件，也没有修改学习记录。Node.js 也需要 22 或更新版本：https://nodejs.org/'
        Write-Host '安装依赖前需要联网；密码和 MFA 只在学校网页输入。使用须知见 docs/使用须知.md。'
    }
    if (-not $NoPause -and -not $Json) { [void](Read-Host '按回车关闭') }
    exit 1
}
$entryArgs = $entryPython.Prefix + @('-B', (Join-Path $PSScriptRoot 'entry.py'), '--mode', $Mode.ToLowerInvariant())
if ($Online) { $entryArgs += '--online' }
if ($Json) { $entryArgs += '--json' }
& $entryPython.Command @entryArgs
$entryExit = $LASTEXITCODE
if (-not $NoPause -and -not $Json -and ($Mode -eq 'Diagnose' -or $entryExit -ne 0)) {
    [void](Read-Host '按回车关闭')
}
exit $entryExit
