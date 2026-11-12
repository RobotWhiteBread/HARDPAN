# SILT: outside audio for HARDPAN

Silt is fine sediment that water carries in from somewhere else and lays down
on top of the hardpan. SILT is the part of HARDPAN that brings in audio from
outside the synth and plays it as an instrument: your own recordings,
AI-generated files, tracks you own copies of, and sample packs.

The target is dark ambient score work: drones, smeared textures, and slow
loops to play guitar and bass over.

---

## Where it lives

| Part | Where | Why |
|---|---|---|
| Ingest, library, reference analysis, exports (S1, S5, S6) | `silt.py`, Python | Offline file work; no real-time deadline |
| Sampler, granular engine, track player and looper (S2, S3, S4) | The Rust plugin (`plugin/`) | Playing them live, with guitar coming in through REAPER, needs the real-time engine |

Everything is converted on import to one internal format, 48 kHz float32 WAV,
with the original kept untouched beside it. The plugin then only ever reads
that one format, and needs no MP3 or FLAC decoder.

The library defaults to `Music\HARDPAN Library` in your user folder
(`HARDPAN_LIBRARY` or `--library` overrides it). It lives outside the
repository on purpose: the repository is going public, and a commercial
recording must never be pushed by accident. `.gitignore` also excludes
`library/` and `exports/` as a backstop.

## Rules

- Never load, decode or allocate in the audio callback. Load on a separate
  thread and hand finished buffers over.
- Files come from your own disk only. No feature downloads, streams or rips
  audio.
- Tests cover ingest conversion, loop point accuracy, and both export paths.
- Any new dependency gets named before it is added. S1 needed none:
  `soundfile`'s bundled libsndfile already reads WAV, FLAC, MP3 and OGG.

## Before anything leaves the house

At home, experiment with anything. Before a piece is released or posted, every
source in it must be yours, licensed, or cleared:

| Source | Default `cleared_for_release` | Why |
|---|---|---|
| Your own recording | yes | It is yours, including captures of your own gear |
| AI-generated | no | Each tool's terms differ; read them, then set it |
| Commercial recording | no | Release needs permission from the recording's owner and the song's publisher, however short the sample |
| Licensed sample pack | yes | Sold for use in released music |

A style or a sound belongs to no one. Studying a track's tempo, key and tone
and building your own patch to get there (S5) is the cleanest way to chase a
sound. This is general orientation, not legal advice.

---

## Phases

### S1: ingest and library (built)

```
python silt.py ingest PATH [PATH ...] --source own|ai|commercial|pack
                      [--title T] [--artist A] [--prompt P] [--license L]
                      [--tag TAG] [--cleared yes|no]
python silt.py list [--source TYPE] [--tag TAG]
python silt.py search TEXT
python silt.py show ID
python silt.py tag ID [--add TAG ...] [--remove TAG ...]
python silt.py set ID [--title T] [--artist A] [--prompt P] [--license L] [--cleared yes|no]
```

Each item is a folder: `original.<ext>` (byte for byte), `audio.wav` (48 kHz
float32, mono or stereo) and `item.json` (title, source type, artist or tool,
prompt, license notes, `cleared_for_release`, tags, and the original's format,
rate, length and SHA-256). Importing the same file twice is detected and
skipped.

Sample-rate conversion is a hand-written polyphase windowed-sinc resampler
(128 taps per phase, 100 dB stopband). From 44.1 kHz it passes everything to
about 19.9 kHz flat and is fully down by 22.05 kHz. Five minutes of stereo
converts in about 5 s.

Tests: `tests/test_silt.py`.

### S2: sampler voice

Play a library item from MIDI notes, pitched by resampling, with loop points,
reverse and start offset, through HARDPAN's filter, envelopes and effects. The
plugin finds items in the library folder and loads them on a background
thread.

### S3: granular engine

Position, grain size, density, position spray, pitch and spread, with MIDI CC
learn so the knobs on a MIDI keyboard can be assigned.

### S4: track player and looper

Play a full track over a loop region while live guitar or bass passes
through, with overdub looping of the live input aligned to the loop length.

### S5: reference mode

Analyze a track without sampling it: tempo, key, loudness over time and
spectral balance, written up as a short report to design a patch from.

### S6: export (hardware export built)

Hardware export: selected items as mono 16-bit 32 kHz WAV, 24 s at most each,
with a running total against a 3.5 minute budget. These are the limits of the
Arturia MicroFreak's user samples.

```
python silt.py hardware ID [ID ...] [--out DIR] [--start S] [--seconds S] [--normalize]
```

Files go to `exports\microfreak` unless `--out` says otherwise. Stereo is
averaged to mono, an excerpt cut out of something longer gets a 10 ms fade at
each cut, and the 16-bit conversion is dithered. The budget counts what is
already in the folder; an item that would not fit is skipped and reported.
Exporting an item again replaces its file. An item that is not cleared for
release still exports, with a warning, since loading it into your own synth is
not releasing it.

Mix export (not built): list every source in a finished piece and warn clearly
about any that is not cleared for release.
