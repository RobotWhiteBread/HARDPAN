## Run-SampleAnalysis.ps1
## Measures every sample in the library (tempo, key, note, brightness, punch,
## what it sounds like) and writes samples\_library\features.csv.
## Safe to stop (Ctrl+C) and rerun: it picks up where it left off.
##
##   powershell -ExecutionPolicy Bypass -File K:\HARDPAN\soundlab\Run-SampleAnalysis.ps1
##   add  -Test   to try 300 files first

param([switch]$Test)

$scripts = $PSScriptRoot
$catalog = 'C:\HARDPAN\studio_setup\samples\_library\catalog.csv'

if (-not (Test-Path $catalog)) { "No catalog yet. Let Build-SampleLibrary.ps1 finish first."; exit 1 }

## Find Python (the py launcher first, then python)
$py = $null
foreach ($c in 'py', 'python', 'python3') {
  $cmd = Get-Command $c -ErrorAction SilentlyContinue
  if ($cmd -and $cmd.Source -notmatch 'WindowsApps') { $py = $cmd.Source; break }
}
if (-not $py) {
  "Python not found. Install it with:"
  "  winget install -e --id Python.Python.3.12"
  "then close and reopen PowerShell and run this again."
  exit 1
}
"Using $py"
& $py -m pip install --quiet --disable-pip-version-check numpy scipy soundfile
if ($LASTEXITCODE -ne 0) { "pip install failed (see above)"; exit 1 }

$workers = [Math]::Max(1, [Environment]::ProcessorCount - 1)
$args2 = @("$scripts\analyze_samples.py", '--workers', $workers)
if ($Test) { $args2 += @('--limit', '300') }

$t = Get-Date
& $py @args2
"Finished in {0:N0} minutes" -f ((Get-Date) - $t).TotalMinutes

## Quick summary
$f = Import-Csv 'C:\HARDPAN\studio_setup\samples\_library\features.csv'
""
"Files analyzed: $($f.Count)   errors: $(($f | ? error).Count)"
$f | Group-Object sound_guess | Sort-Object Count -Descending | Select-Object Name, Count | Format-Table -AutoSize
"With tempo: $(($f | ? tempo_bpm).Count)   With key: $(($f | ? key).Count)   With note: $(($f | ? note).Count)"
