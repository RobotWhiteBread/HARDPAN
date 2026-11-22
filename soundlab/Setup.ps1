## Setup.ps1: one-time setup on Windows
##   powershell -ExecutionPolicy Bypass -File .\Setup.ps1 -Samples "D:\Samples"
## Installs the Python packages, writes soundlab.json pointing at your sample folder,
## and tells REAPER scripts where things live. Rerun any time; it keeps an existing soundlab.json.
param([string]$Samples = "$env:USERPROFILE\Music\Samples", [string]$Projects = "$env:USERPROFILE\Music\soundlab_projects")
$here = $PSScriptRoot
$py = $null
foreach ($c in 'py', 'python', 'python3') {
  $cmd = Get-Command $c -ErrorAction SilentlyContinue
  if ($cmd -and $cmd.Source -notmatch 'WindowsApps') { $py = $cmd.Source; break }
}
if (-not $py) { "Python not found. Install it with:  winget install -e --id Python.Python.3.12   then reopen PowerShell."; exit 1 }
& $py -m pip install --quiet --disable-pip-version-check -r "$here\requirements.txt"
if ($LASTEXITCODE -ne 0) { "pip install failed (see above)"; exit 1 }
$cfg = "$here\soundlab.json"
if (-not (Test-Path $cfg)) {
  $j = [ordered]@{
    sample_folders = @(@{ name = (Split-Path $Samples -Leaf); path = ($Samples -replace '\\','/') })
    library  = (Join-Path $Samples '_library') -replace '\\','/'
    projects = $Projects -replace '\\','/'
    reaper   = 'C:/Program Files/REAPER (x64)/reaper.exe'
    downloads = "$env:USERPROFILE\Downloads" -replace '\\','/'
  }
  $j | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 $cfg
  "Wrote $cfg. Open it to add more sample folders."
} else { "Keeping your existing $cfg" }
& $py "$here\soundlab.py" paths
""
"Ready. Next:"
"  $py `"$here\soundlab.py`" all"
