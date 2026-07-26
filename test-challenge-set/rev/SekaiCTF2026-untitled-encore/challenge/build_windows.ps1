$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Dist = Resolve-Path (Join-Path $Here "..\dist") -ErrorAction SilentlyContinue
if (-not $Dist) {
    $DistPath = Join-Path $Here "..\dist"
    New-Item -ItemType Directory -Force $DistPath | Out-Null
    $Dist = Resolve-Path $DistPath
}

$BuildRoot = $Here
if ($Here.StartsWith("\\")) {
    $BuildRoot = Join-Path $env:TEMP "untitled-encore-src-build"
    if (Test-Path $BuildRoot) {
        Remove-Item -Recurse -Force $BuildRoot
    }
    New-Item -ItemType Directory -Force $BuildRoot | Out-Null
    Copy-Item -Recurse (Join-Path $Here "*") $BuildRoot
}

Set-Location $BuildRoot

cmake -S . -B build-windows -G "Visual Studio 17 2022" -A x64
cmake --build build-windows --config Release

Copy-Item build-windows\Release\untitled-encore-native.exe (Join-Path $Dist "untitled-encore.exe") -Force
