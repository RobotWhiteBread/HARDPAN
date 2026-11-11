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
.venv\Scripts\python -m pip install numpy sounddevice soundfile mido python-rtmidi
.venv\Scripts\python hardpan_proto.py
```

`hardpan_proto.py` renders a C major arpeggio to `hardpan_proto.wav` and plays
it through the default output device.

`.venv\Scripts\python hardpan_proto.py live` plays it live from any connected
MIDI keyboard. With none connected, it opens a small window that turns the
computer keyboard into a piano (A S D F G H J K L ; ' for the white keys, W E
T Y U O P for the black, from C4). An optional block size can follow `live`,
for example `live 128`.
