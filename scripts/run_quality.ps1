[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$QualityArguments
)

. (Join-Path $PSScriptRoot "load_environment.ps1")
& $env:MEDIAFLOW_PYTHON -m scripts.ci.run_quality @QualityArguments
exit $LASTEXITCODE
