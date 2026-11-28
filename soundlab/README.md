# soundlab

A new way to see and use the sample library you already own.

**Works with any DAW.** The Sound Galaxy, Find a sound, the cart and Used vs owned run in your browser on any folder of samples (WAV, AIFF, FLAC), on Windows, Mac or Linux. Saved carts become plain folders you can browse from Ableton, FL Studio, Logic, Bitwig or anything else. REAPER users get extra tools: lay out a cart as a project, build kits, and upgrade samples in existing projects. Used vs owned reads REAPER projects today; Ableton and FL Studio project support is planned.

Most producers reach for a tiny slice of what they own. On the author's machine,
177 REAPER projects use 901 of about 185,000 sounds: half of one percent. soundlab
measures every sample you own, lays them all out on one 3D map so similar sounds
sit together, shows you which ones you actually use, and helps you find the
right sound fast when you need it. Hover to hear, click for the closest matches,
ask for "a dark, short snare in F minor" and audition the shortlist with the
arrow keys, gather a cart that tells you what it is missing, and build kits and
songs in REAPER from what you find.


It runs on your own machine, on your own files. Nothing is uploaded.

## What you get

| Tool | What it does |
|---|---|
| **Library builder** | Catalogs every sample in your folders (type, pack, drum machine, tempo and key from the file names) and makes browse folders for REAPER's Media Explorer out of hard links, so they take no extra disk space. |
| **Analyzer** | Measures every sound: tempo, key, note, brightness, punch, low end, stereo width, noise, attack. Guesses what it is (kick, hat, loop, bass...) and tags its character (dark, lofi, punchy...). Resumable, uses all your CPU cores. |
| **Sound Galaxy** | Every sample as a point in 3D. Similar sounds form named islands ("lofi dark loops"), each sound type can get its own mini galaxy side by side, or pick your own axes. Hover to hear and see the waveform. Click for a web to the 14 closest sounds. Drift from sound to sound. Match key and tempo. Build a kit around one sound. Find duplicate files and gaps in your collection. |
| **Used vs owned** | Reads your REAPER projects and lights up the sounds you have actually used. Most producers reach for a tiny slice of what they own; the rest of the map shows what you paid for and never heard. Show only used or never-used sounds, see how much of each type you use, or press "Surprise me" to land on a random island you have never touched and build something from it. Set `project_folders` in soundlab.json if your projects live elsewhere. |
| **Drop a file** | Drag any audio file onto the page (a phone recording, a sound from a song, a sample you found online) and the Galaxy measures it in your browser, flies to the closest sound you own, and lists the 300 nearest. Nothing is uploaded. |
| **Search by mouth** | Press the button and beatbox or hum for two seconds; the Galaxy trims the recording to the sound and finds what you own that is closest, the same way a dropped file is measured. Hum a low note and you get bass and melodic sounds near that pitch. |
| **Weighted "sounds like this"** | Six sliders (tone, punch and attack, low end, length, width, tempo) set what "similar" means: turn tone up and length off to find the same color in a shorter hit. |
| **Your own recordings** | Add a folder of your recordings as "Mine", then Color by vendor to see where they land among the packs and which pack sounds sit closest to them. |
| **Mix Stage** | Point it at a REAPER project or a folder of stems from any DAW and it draws the mix as a 3D stage you can fly around: left to right is pan, low to high is pitch, front to back is depth (loud, punchy parts up front, quiet washed-out parts behind), and every part glows with the music while the song plays. A flat 2D view is one click away. It marks where parts clash (for example kick and bass fighting at 97 Hz), finds the open spots, and suggests sounds from your own library that fit each one. Each section (from your REAPER markers) gets its own stage and an over-time grid shows how the space changes. Click a suggestion and it plays inside your song, panned and filtered into the open spot, in time. A switch limits suggestions to your own recordings. `py soundlab.py stage song.rpp` |
| **Beat playground** | A 16-step beat (kick, snare, hat, perc and a layer for loops or tones) that keeps playing while you explore: every sound you hover or arrow to swaps into its row, so you hear it in a groove instead of on its own. Loops are varispeeded to the tempo. Lock the rows you like, reroll the rest with similar sounds, fill it from your cart, adjust swing and tempo, and send the kit to your cart. Run `py soundlab.py serve` to use it. |
| **Voice Pads** | Beatbox or hum into any mic and hear sounds from your own library. Teach each pad your kick, snare and hat (six of each, about 20 seconds), then beatbox and the right sample plays. "Match my mouth" finds the library sound closest to the one you made. Hum mode tracks your pitch, snaps it to a key and scale and plays a synth line, with optional chords and glide. A loop recorder with a count-in layers passes, snaps them to the grid, and exports a MIDI file for REAPER (drums on channel 10, melody on channel 1). It can also play REAPER live through a virtual MIDI port such as loopMIDI. Everything runs in your browser; no audio leaves your computer. `py soundlab.py voice` |
| **Play it from a controller** | Plug in a MIDI keyboard or pad controller (built and tested for the Arturia MiniLab 3 layout) and the pages become an instrument. In Voice Pads the pads play the drum pads and the keys play the melody sound, and both record into the loop. In the Galaxy, pads 1 to 5 play the beat playground rows (tick "record pads" to write what you play into the pattern), pads 6 to 8 play the sounds closest to the one you picked, and the keys play the selected sound up and down in pitch, so any one-shot becomes a playable instrument. A small MIDI badge in the corner shows what it hears. |
| **One tool bar** | The Galaxy, Voice Pads, Mix Stage and Mix Check pages share a menu at the top. Mixes lists every song you have staged and every Mix Check report, newest first, so any of them opens in one click from any page. How it works is a flow chart of the whole pipeline, from a downloaded pack to a rendered song, explained three ways: plain English, the science, and in music terms. |
| **Working together** | Send to Voice Pads puts the selected sound on the matching pad. The beat playground's To Voice Pads button opens the beat in the Voice Pads recorder (sounds, pattern, tempo and swing) so you can beatbox or hum on top and export it all as one MIDI file. Voice Pads can fill its pads from your cart and save the pads back as a "Voice kit" cart for REAPER. |
| **Find a sound** | A shortlist for when you need something now. Pick what you need (kick, snare, loop...), move a few sliders (dark to bright, short to long, soft to punchy, thin to heavy, tonal to noisy), set a key or tempo, and get the best matches with waveforms. Arrow keys move and play, Enter drops a sound in the cart, + and - mean "more like this" and "less like this" and re-rank the list. Press B and drag a box on the map to list everything inside it. File and folder names from the pack makers back up the audio guesses. |
| **Cart** | Shift+click, Cart mode (key C) or Enter in the shortlist drops sounds in a cart. Keep a cart per song or mood, star the keepers, and play the whole cart through. The cart tells you how it sounds overall (dark, punchy, lofi...), its key and loop tempo, flags anything that clashes, and shows the slots it is missing: click "+ Hats" to list hats that match the rest of the cart. Save it, then lay it out in REAPER or get a Cart folder for the Media Explorer. |
| **Kit Builder** (REAPER) | Turns a saved kit into a 32 bar song: intro, verses, hook, outro, in one of four grooves (boom bap, house, electro, half-time), loops stretched to one tempo, a starting mix. |
| **Project audit and upgrade** (REAPER) | Reads your REAPER projects, flags loops stretched far from their tempo, and adds two library alternatives to every sample as extra takes (select an item, press T to flip). Your originals are never overwritten. |
| **Mix Check** | A report for any song: loudness (BS.1770), true peak, punch, loudness range, low end in mono, stereo width, tonal balance against your reference tracks, and kick and bass clash, with a spinnable 3D spectrogram. |

## What is new here, and what is not

Good tools already tag and search sample libraries (ADSR Sample Manager, Loopcloud),
map drum hits by similarity (XLN XO, Algonaut Atlas) and find similar sounds
(Sononym). soundlab borrows those ideas and adds a few that are hard to find elsewhere:

- **Used vs owned.** It reads your own REAPER projects and shows which sounds you
  have actually used, so the rest of the map becomes an invitation instead of clutter.
- **A cart that knows what it is missing.** Gather sounds per song or mood; the cart
  reports its overall feel, key and tempo, flags clashes, and fills an empty slot
  ("+ Hats") with sounds that match the rest of the cart.
- **Sound families and vendor bridges.** Near-identical versions (tape, saturated, pitched) collapse into one sound, so you see how many distinct sounds you really own (here, about 97,000 distinct sounds out of 185,000 files). Every sound also shows its closest match from a different vendor, or is marked as a signature sound when nothing else you own comes close.
- **Your whole library, every type, on one map,** across every pack and vendor you own.
- **Open methods.** Every measure and threshold is written out in [METHODS.md](METHODS.md).
  It is plain signal math on your own machine: no account, no upload, no subscription.

## Quick start (about 5 minutes plus measuring time)

You need [Python 3](https://www.python.org/downloads/) and, for the song tools, [REAPER](https://www.reaper.fm/).

Don't know where your samples are? `py soundlab.py setup` looks through the usual places (Music, Documents, Splice, Native Instruments and others; add `--deep` for every drive), asks about each folder it finds and writes `soundlab.json` for you. Add `--mine "D:\Recordings"` to include your own recordings. It only reads folder and file names.

**Windows** (PowerShell, in the soundlab folder):
```powershell
powershell -ExecutionPolicy Bypass -File .\Setup.ps1 -Samples "D:\Samples"
py soundlab.py all
```

**Mac or Linux:**
```bash
./setup.sh ~/Music/Samples
python3 soundlab.py all
```

`all` builds the library, measures every sound, makes the waveforms and opens
the Galaxy in your browser. Measuring runs at roughly 50 to 100 sounds per second
on a modern laptop (about 30 minutes for 170,000 samples). Stop it any time and
run it again; it continues where it left off.

Have samples in more than one place? Open `soundlab.json` and add them:
```json
"sample_folders": [
  { "name": "Drum packs", "path": "D:/Samples/Drums" },
  { "name": "Splice",     "path": "~/Splice/sounds" }
]
```

## Everyday commands

```
python soundlab.py build                 after adding new packs
python soundlab.py analyze               measures only the new sounds
python soundlab.py galaxy                refresh and open the Galaxy (add --waveforms after new packs)
python soundlab.py check song.wav        mix report (add --refs myrefs to compare against songs you like)
python soundlab.py audit                 check your REAPER projects and find alternatives
python soundlab.py cart                  newest saved cart -> a Cart folder for REAPER's Media Explorer
python soundlab.py voice                 Voice Pads: beatbox and hum into your own sounds, export MIDI
```

`python soundlab.py serve` opens the Galaxy through a small server that only your own computer can reach. It is needed for the beat playground; everything else also works when the page is opened as a file.


## In REAPER

Load these with **Actions > New action > Load ReaScript**. They find your folders
through `reaper/soundlab_paths.lua`, which setup writes from `soundlab.json`.

| Script | Use |
|---|---|
| `reaper/Soundlab_Kit_Builder.lua` | Build a song from the newest kit saved in the Galaxy |
| `reaper/Soundlab_Build_All_Kits.lua` | Build every kit in `<projects>/kits`, one tab each |
| `reaper/Soundlab_Cart_To_Project.lua` | Lay out your saved cart on tracks grouped by type |
| `reaper/Soundlab_Upgrade_Project.lua` | Add library alternatives to every sample as takes (run `audit` first) |

## Galaxy keys

| | |
|---|---|
| Hover | hear it, see its waveform |
| Click | select it: closest matches, island, copies, kit ideas |
| Shift+click, or C for Cart mode | add to cart |
| Space | play or stop |
| Esc | clear the selection |

## Examples

`examples/` holds tools that build on the pieces above. Public images and videos only ever
use sounds listed in `public_allow` in your settings: your own recordings, openly licensed
sets, or packs whose makers have said yes in writing.

| Example | Needs |
|---|---|
| `galaxy_demo/make_demo.py`: demo video and photos | ffmpeg, Pillow, sounds of your choice |
| `ufo_cowboy_crate/`: a made-up crate of 134 synthesized UFO and cowboy sounds from five invented vendors, measured by the real pipeline. Open `index.html` to try the crate view with no library of your own | a browser |

## Roadmap

What is next lives in [ROADMAP.md](ROADMAP.md).

## Methods

Every measure, threshold and map step is written out in [METHODS.md](METHODS.md). In the Galaxy, the "How this works" button shows the short version.

## Privacy and licenses

Your settings, catalog, measurements, Galaxy data, reference tracks and reports
stay on your machine; `.gitignore` keeps them out of git. Sample packs are
usually licensed for making music, not for sharing, so never commit audio.

The Galaxy and Mix Check pages load [three.js](https://threejs.org/) (MIT) from
a CDN, so the first open needs internet.

