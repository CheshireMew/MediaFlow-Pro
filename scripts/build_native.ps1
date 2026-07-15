param(
    [string]$BuildDir = "D:\Tools\MediaFlow\build\native-qt611",
    [string]$QtDir = "D:\Tools\MediaFlow\qt",
    [string]$VsDir = "D:\Tools\MediaFlow\toolchains\vs2022"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$source = Join-Path $root "mediaflow\desktop\native"
$vcvars = Join-Path $VsDir "VC\Auxiliary\Build\vcvars64.bat"
$cmake = "D:\Tools\MediaFlow\.venv\Scripts\cmake.exe"
$ninja = "D:\Tools\MediaFlow\.venv\Scripts\ninja.exe"

if (-not (Test-Path $vcvars)) {
    throw "MSVC environment was not found at $vcvars"
}
if (-not (Test-Path (Join-Path $QtDir "lib\cmake\Qt6\Qt6Config.cmake"))) {
    throw "Qt SDK was not found at $QtDir"
}

New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
$configure = "call `"$vcvars`" && `"$cmake`" -S `"$source`" -B `"$BuildDir`" -G Ninja -DCMAKE_MAKE_PROGRAM=`"$ninja`" -DCMAKE_PREFIX_PATH=`"$QtDir`" -DCMAKE_BUILD_TYPE=Release"
$build = "call `"$vcvars`" && `"$cmake`" --build `"$BuildDir`" --config Release"
cmd.exe /d /s /c $configure
if ($LASTEXITCODE -ne 0) { throw "Native preview configure failed" }
cmd.exe /d /s /c $build
if ($LASTEXITCODE -ne 0) { throw "Native preview build failed" }

$plugin = Join-Path $BuildDir "qml\MediaFlow\Native\mediaflownativeplugin.dll"
if (-not (Test-Path $plugin)) {
    throw "Native preview plugin was not produced: $plugin"
}
Write-Output $plugin
