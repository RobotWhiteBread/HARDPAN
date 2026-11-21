## Open-Galaxy.ps1: refreshes the Sound Galaxy data and opens it in your browser
##   powershell -ExecutionPolicy Bypass -File K:\HARDPAN\soundlab\galaxy\Open-Galaxy.ps1
##   add  -Waveforms  the first time (and after new packs) to make the hover waveforms
param([switch]$Waveforms)
$here = $PSScriptRoot
$py = (Get-Command py -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command python -ErrorAction SilentlyContinue).Source }
if ($Waveforms) { & $py "$here\make_thumbs.py" }
& $py "$here\make_galaxy.py"
Start-Process "$here\index.html"
