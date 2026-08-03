param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ApplicationArguments
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "load_environment.ps1")

if (-not (Test-Path -LiteralPath $env:MEDIAFLOW_PYTHON -PathType Leaf)) {
    throw "MediaFlow Pro Python environment was not found: $env:MEDIAFLOW_PYTHON"
}

& $env:MEDIAFLOW_PYTHON -m mediaflow.desktop.app @ApplicationArguments
exit $LASTEXITCODE
