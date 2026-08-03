param(
    [string]$EnvironmentFile = ""
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
if (-not $EnvironmentFile) {
    $EnvironmentFile = if ($env:MEDIAFLOW_ENV_FILE) {
        $env:MEDIAFLOW_ENV_FILE
    } else {
        Join-Path $repositoryRoot ".env"
    }
}

if (Test-Path -LiteralPath $EnvironmentFile -PathType Leaf) {
    $lineNumber = 0
    foreach ($original in Get-Content -LiteralPath $EnvironmentFile -Encoding UTF8) {
        $lineNumber += 1
        $line = $original.Trim()
        if (-not $line -or $line.StartsWith("#")) {
            continue
        }
        if ($line -notmatch '^([A-Z][A-Z0-9_]*)=(.*)$') {
            throw "Invalid environment entry at ${EnvironmentFile}:${lineNumber}"
        }
        $name = $Matches[1]
        $value = $Matches[2].Trim()
        if (
            $value.Length -ge 2 -and
            (($value.StartsWith('"') -and $value.EndsWith('"')) -or
             ($value.StartsWith("'") -and $value.EndsWith("'")))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if (-not [Environment]::GetEnvironmentVariable($name, "Process")) {
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
} elseif ($env:MEDIAFLOW_ENV_FILE) {
    throw "MediaFlow environment file was not found: $EnvironmentFile"
}

$required = @(
    "MEDIAFLOW_DEV_ROOT",
    "MEDIAFLOW_PROJECT_ROOT",
    "MEDIAFLOW_MEDIA_ROOT"
)
foreach ($name in $required) {
    if (-not [Environment]::GetEnvironmentVariable($name, "Process")) {
        throw "$name is required. Copy .env.example to .env and configure this machine."
    }
}

if (-not $env:MEDIAFLOW_PYTHON) {
    $env:MEDIAFLOW_PYTHON = Join-Path $env:MEDIAFLOW_DEV_ROOT ".venv\Scripts\python.exe"
}
if (-not $env:MEDIAFLOW_RUNTIME_DIR) {
    $env:MEDIAFLOW_RUNTIME_DIR = Join-Path $env:MEDIAFLOW_DEV_ROOT "runtime"
}
if (-not $env:MEDIAFLOW_TEST_ROOT) {
    $env:MEDIAFLOW_TEST_ROOT = Join-Path $env:MEDIAFLOW_DEV_ROOT "test-runs"
}
if (-not $env:MEDIAFLOW_TEST_FIXTURE_ROOT) {
    $env:MEDIAFLOW_TEST_FIXTURE_ROOT = Join-Path $env:MEDIAFLOW_DEV_ROOT "test-fixtures"
}
if (-not $env:MEDIAFLOW_NATIVE_BUILD_DIR) {
    $env:MEDIAFLOW_NATIVE_BUILD_DIR = Join-Path $env:MEDIAFLOW_DEV_ROOT "build\native-qt611"
}
if (-not $env:QT_ROOT_DIR) {
    $env:QT_ROOT_DIR = Join-Path $env:MEDIAFLOW_DEV_ROOT "qt"
}
if (-not $env:PIP_CACHE_DIR) {
    $env:PIP_CACHE_DIR = Join-Path $env:MEDIAFLOW_DEV_ROOT "pip-cache"
}
