param(
    [Parameter(Mandatory = $true)]
    [string]$RuntimeRoot,
    [string]$ContractPath = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if ($ContractPath) {
    throw "Runtime contract overrides are not supported; runtime.lock.json is the unique source"
}
$python = if ($env:MEDIAFLOW_PYTHON) { $env:MEDIAFLOW_PYTHON } else { "python" }
& $python (Join-Path $PSScriptRoot "prepare_runtime.py") --runtime-root $RuntimeRoot
if ($LASTEXITCODE -ne 0) {
    throw "Pinned runtime preparation failed"
}
