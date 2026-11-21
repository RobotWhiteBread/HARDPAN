## Make-New-Tracks.ps1: refresh the project audit, the Machine Tour kits and the Mars remixes
##   powershell -ExecutionPolicy Bypass -File K:\HARDPAN\soundlab\reaper\Make-New-Tracks.ps1
##   add  -All  to remix every song instead of the default dozen
param([switch]$All)
$py = (Get-Command py -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command python -ErrorAction SilentlyContinue).Source }
& $py "$PSScriptRoot\project_audit.py"
& $py "$PSScriptRoot\make_kits.py"
if ($All) { & $py "$PSScriptRoot\remix_projects.py" --all } else { & $py "$PSScriptRoot\remix_projects.py" }
"Next, in REAPER: run HARDPAN_Build_All_Kits.lua for the Machine Tour,"
"and open any song in C:\HARDPAN\studio_setup\projects\remix_mars for the remixes."
