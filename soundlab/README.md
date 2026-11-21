# HARDPAN soundlab

Tools that measure a sample library and use the numbers to explore sounds,
build tracks in REAPER, and check mixes.

| Part | What it does | Run |
|---|---|---|
| Analyzer | Measures every sample: tempo, key, note, brightness, punch, low end, stereo width, noise, a sound-type guess and character tags. Resumable. | `Run-SampleAnalysis.ps1` (`-Test` for 300 files) |
| Sound Galaxy | Every sample as a point in 3D. Similarity islands with names ("lofi dark loops"), or pick your own axes. Hover to hear and see the waveform; click for a web to the 14 closest sounds, Drift to wander sound to sound, Match key and tempo, and Build a kit around this. Pack names, one-of-each to hide the extra copies, and Gaps to show the kinds of sounds you do not have yet. Save a kit for REAPER. | `galaxy\Open-Galaxy.ps1` (add `-Waveforms` the first time) |
| Kit Builder | Turns a kit into a 32 bar REAPER session in one of four grooves (boombap, house, electro, halftime): intro, verses, hook, outro, loops stretched to one tempo, a starting mix. | REAPER: `reaper\HARDPAN_Kit_Builder.lua` |
| Machine Tour | Eight new songs, one per classic drum machine (SP-1200, MPC60, LinnDrum, 909, 808, DMX, CR-78, Drumulator), with a melodic loop near the tempo and bass and keys in its key. | `reaper\Make-New-Tracks.ps1`, then REAPER: `HARDPAN_Build_All_Kits.lua` |
| Mars Remixes | Copies of your songs with the drums rebuilt from Samples From Mars: hits matched by sound, breaks swapped for Mars loops near the same tempo with every chop re-timed. Originals untouched. | `reaper\Make-New-Tracks.ps1` |
| Project Upgrade | Adds two library alternatives to every sample in a song as extra takes (press T to flip). | REAPER: `reaper\HARDPAN_Upgrade_Project.lua` |
| Mix Check | Report page with a spinnable 3D spectrogram, loudness (BS.1770), true peak, punch, loudness range, low end in mono, stereo width, tonal balance and kick and bass clash. | `mixmath\Check-Mix.ps1 song.wav` |

## Flow

1. `studio_setup\scripts\Build-SampleLibrary.ps1` builds `_library` and `catalog.csv` (hard links, no extra space).
2. `Run-SampleAnalysis.ps1` measures every file into `features.csv`.
3. `galaxy\Open-Galaxy.ps1` turns that into the Galaxy and opens it.
4. Pick sounds, Save kit, run the Kit Builder in REAPER.
5. Render the song, run Mix Check, fix what it flags.

## Reference tracks

Put a few songs whose sound you like in `soundlab\refs` and pass `-Refs` to
Mix Check. Their tonal balance becomes the target instead of the built-in rough
guide. They are for comparison only and are never committed.

## Data stays local

Samples, `catalog.csv`, `features.csv`, the Galaxy data file, reference tracks
and mix reports all stay on this machine. Sample packs are licensed for making
music, not for redistribution, so only code lives in this repo.

## Needs

Python 3 with numpy, scipy and soundfile (the analyzer installs them). The
Galaxy and Mix Check pages load three.js from unpkg, so the first open needs
internet.
