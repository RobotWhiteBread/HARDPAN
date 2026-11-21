# HARDPAN soundlab

Tools that measure a sample library and use the numbers to build better
tracks and mixes in REAPER.

## What is here

| Path | What it does |
|---|---|
| `analyze_samples.py` | Measures every file in the library catalog: tempo, key, note, brightness, punch, low end, stereo width, noise, a sound-type guess and character tags. Writes `features.csv`. Resumable. |
| `Run-SampleAnalysis.ps1` | One-command Windows launcher. Installs numpy, scipy and soundfile, runs the analyzer on all cores, prints a summary. `-Test` does 300 files. |
| `galaxy/` | (next) 3D Sound Galaxy viewer: every sample as a point in space, grouped by how it sounds. |
| `mixmath/` | (next) Mix checks: loudness, tonal balance, kick and bass masking, harmonic fit, groove, arrangement energy. |

## How the pieces fit

1. `studio_setup\scripts\Build-SampleLibrary.ps1` builds the hard-linked
   `_library` and `catalog.csv` (no extra disk space).
2. `Run-SampleAnalysis.ps1` measures every file into `features.csv`.
3. The REAPER builders read `features.csv` to pick kits that match by sound
   and loops that match by key and tempo.

## Data stays local

The library, `catalog.csv`, `features.csv` and all audio live under
`C:\HARDPAN\studio_setup\samples` and are never committed. Sample packs are
licensed for use in music, not for redistribution.

## Run

```powershell
powershell -ExecutionPolicy Bypass -File K:\HARDPAN\soundlab\Run-SampleAnalysis.ps1 -Test
powershell -ExecutionPolicy Bypass -File K:\HARDPAN\soundlab\Run-SampleAnalysis.ps1
```
