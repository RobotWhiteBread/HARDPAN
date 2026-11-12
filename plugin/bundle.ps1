# Build HARDPAN and package it as CLAP and VST3 plugins in target\bundled.
#
#   .\bundle.ps1
#
# On Windows both formats are the same compiled library: a CLAP plugin is the
# DLL renamed to .clap, and a VST3 plugin is the DLL placed inside a folder
# layout (HARDPAN.vst3\Contents\x86_64-win\HARDPAN.vst3).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

cargo build --release
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$dll = "target\release\hardpan.dll"
$out = "target\bundled"
$vst3 = "$out\HARDPAN.vst3\Contents\x86_64-win"
New-Item -ItemType Directory -Force $vst3 | Out-Null
Copy-Item $dll "$out\HARDPAN.clap" -Force
Copy-Item $dll "$vst3\HARDPAN.vst3" -Force

Write-Host ""
Write-Host "Built:"
Write-Host "  $out\HARDPAN.clap"
Write-Host "  $out\HARDPAN.vst3"
Write-Host ""
Write-Host "To install, copy them to:"
Write-Host "  C:\Program Files\Common Files\CLAP\HARDPAN.clap"
Write-Host "  C:\Program Files\Common Files\VST3\HARDPAN.vst3"
