# HARDPAN

**A software synthesizer built from first principles.**

Oscillators, envelopes, filters, and a voice allocator written by hand, then
wrapped as a VST3 plugin or a web audio instrument. No existing synth engine,
no wrappers around someone else's DSP. The point is to learn digital signal
processing from the arithmetic up: what a biquad filter actually does, why
naive sawtooth waves alias, how to anti-alias them, how an ADSR envelope
avoids clicks.

Named for the compacted soil layer under the topsoil. Hard, flat, and the
thing everything else sits on.

## Sound target
Dark and hard-driving, with an 80s edge. On one side, the eerie, ominous
screen scores of Nick Cave and Warren Ellis; on the other, the
pulsing sequenced basslines of John Carpenter's scores and 80s-inspired
darksynth. In synth terms:
- Sawtooth oscillators detuned a few cents apart, with a square
  sub-oscillator an octave down
- A resonant low-pass filter kept mostly closed, snapped open on every note
  by a fast filter envelope
- 16th-note sequences in minor keys, around 100 to 120 BPM
- Drive into the filter for grit
- Chorus, a dark tempo-synced delay, and a long reverb

`BUILD_PROMPT.md` phases 2, 3 and 3b build these, in that order.

## Stack
Rust with `nih-plug`, or Python with `numpy` plus `sounddevice` for
prototyping. The Rust DSP lives in one `no_std` crate, so the same code runs in
the plugin and on a Daisy Seed pedal (`GUITAR_RIG.md`).

## What gets written by hand
Everything that makes or shapes sound: oscillators, envelopes, filters, voice
allocation, the time-based effects (delay, reverb, chorus, flanger, phaser,
tremolo), and the forward pass of any neural capture model HARDPAN runs.

The one exception is *training* neural captures of your own pedals
(`GUITAR_RIG.md` section 4), which uses Neural Amp Modeler or GuitarML's
trainer. Training a network is a measurement process, not the signal
processing this project is about. RTNeural, the runtime those captures
normally play through, serves as the reference HARDPAN's own inference is
checked against, not as a replacement for it.

## Milestones
1. A single-voice sine oscillator with an ADSR envelope that responds to MIDI
   note on/off without audible clicks. (`BUILD_PROMPT.md` phases 1 and 1b.)
2. Polyphony, a resonant low-pass filter, and one decent-sounding preset.
   (Phases 2 and 3.)

## Stretch
Physical modeling of a plucked string, so it can imitate a guitar rather than
a keyboard.

## Why it compounds
DSP underlies every audio tool you will ever use, including bioacoustics work.

## Next step
Prototype in Python first. Get one clean, click-free note out of a sine
oscillator before touching Rust or plugin formats.

## Running the prototype
Build the venv on Python 3.11, not 3.13. `python-rtmidi`, the backend mido
uses for live MIDI input, has no Windows wheel for 3.13 yet.

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m venv .venv
.venv\Scripts\python -m pip install numpy sounddevice soundfile mido python-rtmidi matplotlib
.venv\Scripts\python hardpan_proto.py
```

`hardpan_proto.py` renders a C major arpeggio to `hardpan_proto.wav` and plays
it through the default output device.

`.venv\Scripts\python hardpan_proto.py live` plays it live from any connected
MIDI keyboard. With none connected, it opens a small window that turns the
computer keyboard into a piano (A S D F G H J K L ; ' for the white keys, W E
T Y U O P for the black, from C4). An optional block size can follow `live`,
for example `live 128`.

`.venv\Scripts\python hardpan_proto.py demo` (phase 3) renders the two
presets through the 8-voice filtered synth to `hardpan_bass.wav` (a 16th-note
bassline) and `hardpan_pad.wav` (a slow chord progression), then plays them.
Rendering takes a minute or two, because eight voices of per-sample Python are
slower than real time.

`.venv\Scripts\python hardpan_proto.py track` (phase 3b) renders a 30-second
stereo track to `hardpan_track.wav` and plays it: a sequenced bass with drive,
accents and glides, the pad, and a gliding lead, through chorus, delay and
reverb. Rendering takes about two minutes.

## The plugin (phase 4)

`plugin/` is HARDPAN as a CLAP and VST3 instrument, a Rust port of the
prototype on nice-plug. `plugin\bundle.ps1` builds it into
`plugin\target\bundled\`; see `plugin/README.md` for installing, playing and
testing it.

## SILT: outside audio

`silt.py` brings outside audio into a library HARDPAN can play: your own
recordings, AI-generated files, tracks you own, and sample packs. Each item is
converted to 48 kHz float, keeps its original, and carries a
`cleared_for_release` flag. See `SILT.md` for the phases and the release
rules.

## The hardware rig

`rig_check.py` checks a rig built around a MOTU M2 interface, an Arturia
MiniLab 3 keyboard and an Arturia MicroFreak:

```powershell
.venv\Scripts\python rig_check.py devices    # is everything connected?
.venv\Scripts\python rig_check.py midi       # what is the keyboard sending?
.venv\Scripts\python rig_check.py latency    # round trip through a loopback cable
```

## The vinyl archive

`VINYL_ARCHIVE.md` covers recording a record collection through the M2, a
pipeline to split, tag and sample it, purchased downloads, and a guest mode.
Phase A1 is built: `.venv\Scripts\python vinyl.py record` records one side to a
24-bit FLAC master with live meters and a clipping alarm.

## More prototype tools

`.venv\Scripts\python compare_drive.py` (phase 3b) measures the drive's
aliasing at 1x, 2x and 4x oversampling and writes `drive_comparison.png`.

`.venv\Scripts\python compare_aliasing.py` (phase 2) compares naive and
PolyBLEP saw and square oscillators. It writes `aliasing_comparison.png`
(spectrograms of a 100 Hz to 8 kHz sweep, plus measured aliasing at eight
pitches) and the two saw sweeps as `sweep_naive_saw.wav` and
`sweep_polyblep_saw.wav`, so the aliasing can be heard as well as seen.
