## Check-Mix.ps1: measure a song or bounce and open the mix report
##   powershell -ExecutionPolicy Bypass -File K:\HARDPAN\soundlab\mixmath\Check-Mix.ps1 C:\path\song.wav
##   add  -Refs K:\HARDPAN\soundlab\refs   to compare against reference tracks
##   add  -Kick kick.wav -Bass bass.wav    to check the kick and bass clash
param([Parameter(Mandatory=$true)][string]$File, [string]$Refs, [string]$Kick, [string]$Bass)
$py = (Get-Command py -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command python -ErrorAction SilentlyContinue).Source }
$a = @("$PSScriptRoot\mix_check.py", $File)
if ($Refs) { $a += @('--refs', $Refs) }
if ($Kick -and $Bass) { $a += @('--kick', $Kick, '--bass', $Bass) }
& $py @a
