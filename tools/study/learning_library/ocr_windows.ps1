<#
Local-only OCR candidate text. Run with Windows PowerShell 5.1:
  powershell.exe -NoProfile -File ocr_windows.ps1 -ListLanguages
  powershell.exe -NoProfile -File ocr_windows.ps1 -ImagePath C:\path\page.png -LanguageTag en-US
  powershell.exe -NoProfile -File ocr_windows.ps1 -ManifestPath C:\path\images.json -LanguageTag en-US

Manifest is a UTF-8 JSON array: [{"id":"material:page", "path":"C:/path/page.png"}].
An optional language property overrides LanguageTag per image. Relative image
paths resolve from the manifest directory. One failed image yields an error row;
the batch continues. All successful rows still have verified=false.

No installation, network, state/cache updates, or source-file modifications.
Output is unverified retrieval text. Equations, handwriting, columns, charts and
tables still require direct visual review. OCR confidence is not available from
this engine, so confidence remains null; blank OCR does not establish blankness.
#>
[CmdletBinding()]
param(
    [string]$ImagePath,
    [string]$ManifestPath,
    [string]$LanguageTag = 'en-US',
    [switch]$ListLanguages
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
if ($PSVersionTable.PSEdition -ne 'Desktop') {
    throw 'Use Windows PowerShell 5.1 (powershell.exe), which provides WinRT projection support.'
}
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType=WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Foundation, ContentType=WindowsRuntime]
$available = @([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | ForEach-Object {
    [ordered]@{ tag = $_.LanguageTag; name = $_.DisplayName }
})
if ($ListLanguages) {
    [ordered]@{ engine = 'Windows.Media.Ocr'; languages = $available; max_image_dimension = [Windows.Media.Ocr.OcrEngine]::MaxImageDimension } | ConvertTo-Json -Depth 6 -Compress
    exit 0
}
if ([bool]$ImagePath -eq [bool]$ManifestPath) { throw 'Specify exactly one of ImagePath or ManifestPath.' }
$engines = @{}
function Get-OcrEngine([string]$Tag) {
    if (-not $engines.ContainsKey($Tag)) {
        $language = New-Object Windows.Globalization.Language($Tag)
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
        if ($null -eq $engine) { throw "OCR language is not installed: $Tag" }
        $engines[$Tag] = $engine
    }
    return $engines[$Tag]
}

$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapPixelFormat, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapAlphaMode, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$asyncToTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
function Await-WinRt($Operation, [Type]$ResultType) {
    $task = $asyncToTask.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    try { $task.Wait() }
    catch {
        $message = if ($null -ne $task.Exception) { $task.Exception.Flatten().InnerExceptions | ForEach-Object { $_.Message } } else { $_.Exception.Message }
        throw ($ResultType.FullName + ': ' + ($message -join '; '))
    }
    return $task.Result
}

function Invoke-ImageOcr([string]$Path, [string]$Tag) {
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    $source = (Get-Item -LiteralPath $Path -ErrorAction Stop).FullName
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw 'ImagePath must be an existing image file.' }
    $engine = Get-OcrEngine $Tag
    $stream = $null
    $bitmap = $null
    $memory = $null
    try {
    # Load the explicitly provided local file through normal file permissions.
    # This avoids StorageFile broker access, which is unavailable in some agent
    # sandboxes even for workspace files the process can already read.
    $sourceBytes = [System.IO.File]::ReadAllBytes($source)
    $sourceHasher = [System.Security.Cryptography.SHA256]::Create()
    try { $sourceHash = ([System.BitConverter]::ToString($sourceHasher.ComputeHash($sourceBytes))).Replace('-', '').ToLowerInvariant() }
    finally { $sourceHasher.Dispose() }
    $memory = New-Object System.IO.MemoryStream(,$sourceBytes)
    $stream = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream($memory)
    $decoder = Await-WinRt ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    if ($decoder.PixelWidth -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension -or $decoder.PixelHeight -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension) {
        throw 'Image exceeds Windows OCR dimensions. Render a smaller page image first; this script never resizes source files.'
    }
    $bitmap = Await-WinRt ($decoder.GetSoftwareBitmapAsync([Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8, [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied)) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result = Await-WinRt ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $lines = @($result.Lines | ForEach-Object { $_.Text })
    $timer.Stop()
    [ordered]@{
        schema = 1
        engine = 'Windows.Media.Ocr'
        source_image = $source
        source_sha256 = $sourceHash
        language = $engine.RecognizerLanguage.LanguageTag
        width = $decoder.PixelWidth
        height = $decoder.PixelHeight
        elapsed_ms = $timer.ElapsedMilliseconds
        text = ($lines -join "`n")
        lines = $lines
        text_angle = $result.TextAngle
        confidence = $null
        verified = $false
        errors = @()
        quality_flags = @('ocr_candidate_only', 'needs_visual_review', 'formula_and_handwriting_unverified', 'reading_order_unverified', 'blank_result_does_not_prove_blank_page')
    }
    }
    finally {
        if ($null -ne $bitmap) { $bitmap.Dispose() }
        if ($null -ne $stream) { $stream.Dispose() }
        if ($null -ne $memory) { $memory.Dispose() }
    }
}

if ($ImagePath) {
    Invoke-ImageOcr $ImagePath $LanguageTag | ConvertTo-Json -Depth 8 -Compress
    exit 0
}

$manifestFile = (Get-Item -LiteralPath $ManifestPath -ErrorAction Stop).FullName
$manifestDirectory = Split-Path -Parent $manifestFile
$manifestText = [System.IO.File]::ReadAllText($manifestFile, [System.Text.Encoding]::UTF8)
if (-not $manifestText.TrimStart().StartsWith('[')) { throw 'Manifest must contain a JSON array.' }
$manifest = ConvertFrom-Json -InputObject $manifestText
$batchTimer = [System.Diagnostics.Stopwatch]::StartNew()
$outputRows = New-Object 'System.Collections.Generic.List[object]'
foreach ($item in $manifest) {
    $itemPath = [string]$item.path
    $itemTag = if ($item.language) { [string]$item.language } else { $LanguageTag }
    try {
        if (-not $itemPath) { throw 'Manifest item has no image path.' }
        if (-not [System.IO.Path]::IsPathRooted($itemPath)) { $itemPath = Join-Path $manifestDirectory $itemPath }
        $row = Invoke-ImageOcr $itemPath $itemTag
        $row['id'] = $item.id
    }
    catch {
        $row = [ordered]@{
            id = $item.id
            engine = 'Windows.Media.Ocr'
            source_image = $itemPath
            language = $itemTag
            text = $null
            lines = @()
            confidence = $null
            verified = $false
            errors = @([ordered]@{ type = $_.Exception.GetType().Name; message = $_.Exception.Message })
            quality_flags = @('ocr_failed', 'needs_visual_review')
        }
    }
    $outputRows.Add($row)
}
$batchTimer.Stop()
$failed = @($outputRows | Where-Object { $_.errors.Count -gt 0 }).Count
[ordered]@{
    schema = 1
    engine = 'Windows.Media.Ocr'
    manifest = $manifestFile
    available_languages = $available
    requested = $outputRows.Count
    succeeded = $outputRows.Count - $failed
    failed = $failed
    elapsed_ms = $batchTimer.ElapsedMilliseconds
    verified = $false
    results = @($outputRows.ToArray())
} | ConvertTo-Json -Depth 10 -Compress
