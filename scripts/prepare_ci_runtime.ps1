param(
    [Parameter(Mandatory = $true)]
    [string]$RuntimeRoot,
    [string]$ContractPath = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $ContractPath) {
    $ContractPath = Join-Path $projectRoot "runtime.lock.json"
}
$contract = Get-Content -LiteralPath $ContractPath -Raw | ConvertFrom-Json
if ($contract.schema_version -ne 1) {
    throw "Unsupported runtime lock schema: $($contract.schema_version)"
}
$shotcut = $contract.windows.shotcut
$runtime = [System.IO.Path]::GetFullPath($RuntimeRoot)
$downloadRoot = Join-Path $runtime "downloads"
$dependencyRoot = Join-Path $runtime "deps"
$archiveName = [System.IO.Path]::GetFileName([string]$shotcut.archive_url)
$archive = Join-Path $downloadRoot $archiveName
$installRoot = Join-Path $dependencyRoot ("shotcut-" + $shotcut.version)
$shotcutRoot = Join-Path $installRoot ([string]$shotcut.archive_root)

New-Item -ItemType Directory -Force -Path $downloadRoot, $dependencyRoot | Out-Null
if (-not (Test-Path -LiteralPath $archive)) {
    $download = Join-Path $downloadRoot ("download-" + [guid]::NewGuid().ToString("N") + ".zip")
    Invoke-WebRequest -Uri ([string]$shotcut.archive_url) -OutFile $download
    $downloadHash = (Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($downloadHash -ne ([string]$shotcut.archive_sha256).ToLowerInvariant()) {
        throw "Shotcut download checksum mismatch: $download"
    }
    Move-Item -LiteralPath $download -Destination $archive
}
$archiveHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($archiveHash -ne ([string]$shotcut.archive_sha256).ToLowerInvariant()) {
    throw "Pinned Shotcut archive checksum mismatch: $archive"
}

$required = @("melt.exe", "ffmpeg.exe", "ffprobe.exe", "lib\mlt")
$ready = Test-Path -LiteralPath $shotcutRoot -PathType Container
if ($ready) {
    foreach ($relative in $required) {
        if (-not (Test-Path -LiteralPath (Join-Path $shotcutRoot $relative))) {
            $ready = $false
            break
        }
    }
}
if (-not $ready) {
    if (Test-Path -LiteralPath $installRoot) {
        throw "Incomplete pinned runtime already exists and will not be overwritten: $installRoot"
    }
    $staging = Join-Path $dependencyRoot ("staging-shotcut-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $staging | Out-Null
    Expand-Archive -LiteralPath $archive -DestinationPath $staging
    $stagedRoot = Join-Path $staging ([string]$shotcut.archive_root)
    foreach ($relative in $required) {
        if (-not (Test-Path -LiteralPath (Join-Path $stagedRoot $relative))) {
            throw "Pinned Shotcut archive is missing $relative in $stagedRoot"
        }
    }
    Move-Item -LiteralPath $staging -Destination $installRoot
}

& (Join-Path $PSScriptRoot "prepare_mlt_preview.ps1") -MltRoot $shotcutRoot | Out-Null
$ffmpeg = Join-Path $shotcutRoot "ffmpeg.exe"
$ffprobe = Join-Path $shotcutRoot "ffprobe.exe"
$melt = Join-Path $shotcutRoot "melt.exe"
$environment = [ordered]@{
    MEDIAFLOW_RUNTIME_DIR = $runtime
    MEDIAFLOW_FFMPEG = $ffmpeg
    MEDIAFLOW_FFPROBE = $ffprobe
    MEDIAFLOW_MELT = $melt
}
if ($env:GITHUB_ENV) {
    foreach ($entry in $environment.GetEnumerator()) {
        Add-Content -LiteralPath $env:GITHUB_ENV -Value ("{0}={1}" -f $entry.Key, $entry.Value)
    }
}
[pscustomobject]@{
    runtime_root = $runtime
    shotcut_root = $shotcutRoot
    archive_sha256 = $archiveHash
    environment = $environment
} | ConvertTo-Json -Depth 4
