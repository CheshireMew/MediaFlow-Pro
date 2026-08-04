param(
    [string]$BuildDir = "",
    [string]$QtDir = "",
    [string]$VsDir = "",
    [string]$PythonExecutable = "python"
)

$ErrorActionPreference = "Stop"
if ($VsDir) {
    throw "Visual Studio selection is owned by scripts/build_native.py"
}
$arguments = @((Join-Path $PSScriptRoot "build_native.py"))
if ($BuildDir) { $arguments += @("--build-dir", $BuildDir) }
if ($QtDir) { $arguments += @("--qt-dir", $QtDir) }
& $PythonExecutable @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Native preview build failed"
}
