param(
    [string]$BuildDir = "",
    [string]$QtDir = "",
    [string]$VsDir = "",
    [string]$PythonExecutable = "python"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$source = Join-Path $root "mediaflow\desktop\native"
if (-not $BuildDir) {
    $BuildDir = if ($env:MEDIAFLOW_NATIVE_BUILD_DIR) {
        $env:MEDIAFLOW_NATIVE_BUILD_DIR
    } else {
        "D:\Tools\MediaFlow\build\native-qt611"
    }
}
if (-not $QtDir) {
    $QtDir = if ($env:QT_ROOT_DIR) {
        $env:QT_ROOT_DIR
    } else {
        "D:\Tools\MediaFlow\qt"
    }
}
if (-not $VsDir) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path -LiteralPath $vswhere) {
        $VsDir = (& $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath).Trim()
    }
}
if (-not $VsDir) {
    throw "A Visual Studio installation with the x64 C++ toolchain was not found"
}
$vcvars = Join-Path $VsDir "VC\Auxiliary\Build\vcvars64.bat"
$python = (Get-Command $PythonExecutable -ErrorAction Stop).Source
$pythonRoot = Split-Path -Parent $python
$pythonScripts = if ((Split-Path -Leaf $pythonRoot) -eq "Scripts") {
    $pythonRoot
} else {
    Join-Path $pythonRoot "Scripts"
}
$cmake = Join-Path $pythonScripts "cmake.exe"
$ninja = Join-Path $pythonScripts "ninja.exe"
if (-not (Test-Path -LiteralPath $cmake)) {
    $cmake = (Get-Command cmake -ErrorAction Stop).Source
}
if (-not (Test-Path -LiteralPath $ninja)) {
    $ninja = (Get-Command ninja -ErrorAction Stop).Source
}

if (-not (Test-Path -LiteralPath $vcvars)) {
    throw "MSVC environment was not found at $vcvars"
}
if (-not (Test-Path -LiteralPath $cmake)) {
    throw "CMake from the reviewed Python environment was not found at $cmake"
}
if (-not (Test-Path -LiteralPath $ninja)) {
    throw "Ninja from the reviewed Python environment was not found at $ninja"
}
if (-not (Test-Path -LiteralPath (Join-Path $QtDir "lib\cmake\Qt6\Qt6Config.cmake"))) {
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
