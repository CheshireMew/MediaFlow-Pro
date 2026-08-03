param(
    [string]$MltRoot = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "load_environment.ps1")
if (-not $MltRoot) {
    $MltRoot = if ($env:MEDIAFLOW_MLT_ROOT) {
        $env:MEDIAFLOW_MLT_ROOT
    } elseif ($env:MEDIAFLOW_MELT) {
        Split-Path -Parent $env:MEDIAFLOW_MELT
    } else {
        throw "MEDIAFLOW_MLT_ROOT or MEDIAFLOW_MELT is required to prepare the MLT preview plugin set"
    }
}
$source = Join-Path $MltRoot "lib\mlt"
$destination = Join-Path $MltRoot "lib\mlt-preview"
$archive = Join-Path $MltRoot "archive\mlt-preview-nonplugins"
if (-not (Test-Path $source)) {
    throw "MLT repository was not found: $source"
}
New-Item -ItemType Directory -Force -Path $destination | Out-Null

$excluded = @("libmltqt6.dll", "libmltglaxnimate-qt6.dll")
$pluginNames = @(Get-ChildItem $source -File | Where-Object { $_.Name -notin $excluded } | ForEach-Object Name)

# Older preparation runs placed runtime dependencies in the repository folder.
# MLT scans every DLL in that folder as a plugin, so preserve those files in an
# archive outside the repository before reconstructing the plugin-only view.
Get-ChildItem $destination -File | Where-Object { $_.Name -notin $pluginNames } | ForEach-Object {
    New-Item -ItemType Directory -Force -Path $archive | Out-Null
    $target = Join-Path $archive $_.Name
    if (-not (Test-Path $target)) {
        Move-Item -LiteralPath $_.FullName -Destination $target
    } else {
        Move-Item -LiteralPath $_.FullName -Destination (Join-Path $archive ("stale-" + $_.Name))
    }
}

Get-ChildItem $source -File | Where-Object { $_.Name -notin $excluded } | ForEach-Object {
    $target = Join-Path $destination $_.Name
    if (-not (Test-Path $target)) {
        New-Item -ItemType HardLink -Path $target -Target $_.FullName | Out-Null
    }
}

Write-Output $destination
