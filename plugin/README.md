# HARDPAN plugin

HARDPAN as a CLAP and VST3 instrument for Windows: a Rust port of the Python
prototype (`../hardpan_proto.py`), built on
[nice-plug](https://codeberg.org/RustAudio/nice-plug) 0.4.2.

## Layout

| Path | What it is |
|---|---|
| `dsp/` | `hardpan-dsp`: all of the signal processing. `no_std` and allocation-free, so the same code can later run on a Daisy Seed pedal |
| `dsp/tests/golden.rs` | Checks the Rust port against samples rendered by the Python prototype |
| `dsp/tests/make_golden.py` | Regenerates those reference samples |
| `src/lib.rs` | The plugin itself: parameters, MIDI, the host's tempo, and routing |
| `bundle.ps1` | Builds and packages the plugin |

## Build and install

```powershell
.\bundle.ps1
```

That produces `target\bundled\HARDPAN.clap` and `target\bundled\HARDPAN.vst3`.
To install, copy them into `C:\Program Files\Common Files\CLAP\` and
`C:\Program Files\Common Files\VST3\`, then rescan plugins in your DAW.

## Test

```powershell
cargo test --workspace --release
```

- 30 unit tests on the DSP, asserting on sample values: oscillators, envelope,
  filter, drive, voice allocation, pitch bend, effects, sequencer.
- 8 golden tests: the Rust output against the Python prototype's. Most cases
  match bit for bit. The rest differ by under 1e-4 of their peak, from rounding
  where numpy and Rust's libm compute sin, tanh or a dot product differently.
- 11 plugin tests. The most important one runs every audio path (mono, a chord
  past 8 voices, the sequencer, every controller message, a reset) under an
  allocator that counts, and requires zero allocations. The rest cover levels,
  the sequencer, and the controller: knobs, strips and pedal.
- A minimal CLAP host (`tests/clap_host.rs`) that loads the packaged plugin the
  way a DAW would, plays a note through the real nice-plug callback, and checks
  that it sounds, stays below full scale, and falls silent. Run it after
  `bundle.ps1`:

  ```powershell
  cargo test --release --test clap_host -- --ignored --nocapture
  ```

  Pointing it at a debug build (`$env:HARDPAN_CLAP = "target\debug\hardpan.dll"`)
  also checks that nothing in the audio callback allocates, since debug builds
  fail on any allocation there.

To regenerate the golden data after changing the prototype, run this from the
repository root: `.venv\Scripts\python plugin\dsp\tests\make_golden.py`.

## Playing it

- **The defaults are the sequenced bass** from the prototype's phase 3b track:
  mono with glide, two detuned saws and a square an octave down, driven into a
  resonant 24 dB filter.
- **Mode** switches between Mono (glide, last-note priority) and Poly (8 voices,
  with stealing).
- **Accent:** notes at velocity 102 or above are accented: louder, with more
  filter sweep.
- **Sequencer:** with it on, holding a key plays the bass pattern on that note
  at the host's tempo. Pressing another key transposes the pattern, and letting
  go of every key stops it.
- **Effects:** chorus, a tempo-synced ping-pong delay and reverb. The delay
  and reverb mixes work as sends.

## Playing it from a MIDI keyboard

The mapping is built in and matches an Arturia MiniLab 3 on a factory User
program (Shift + Pad 3 switches program). `CC_MAP` in `src/lib.rs` holds the
numbers for a different controller.

| Control | MIDI | What it does |
|---|---|---|
| Knob 1 | CC 74 | Cutoff |
| Knob 2 | CC 71 | Resonance |
| Knob 3 | CC 76 | Filter envelope amount |
| Knob 4 | CC 77 | Drive |
| Knob 5 | CC 93 | Chorus mix |
| Knob 6 | CC 18 | Delay mix |
| Knob 7 | CC 19 | Delay feedback |
| Knob 8 | CC 16 | Reverb mix |
| Slider 1 | CC 82 | Filter envelope decay |
| Slider 2 | CC 83 | Amp attack |
| Slider 3 | CC 85 | Amp release |
| Slider 4 | CC 17 | Glide |
| Pitch strip | pitch bend | 2 semitones each way |
| Mod strip | CC 1 | Opens the filter by up to 3 octaves |
| Sustain pedal | CC 64 | Holds notes; with the sequencer on, latches the pattern so both hands are free |

Two things to know about the knobs:

- **A knob takes over; it does not move the host's slider.** A plugin cannot
  change its own parameters from the audio thread. From the first turn, the
  synth follows the knob, and the parameter shown in the host stays where it
  was. Changing that parameter in the host hands control back to it.
- **The first turn glides.** The synth moves from the parameter's value to the
  knob's position over about 20 ms, so a knob that starts somewhere else does
  not click, though the sound does move there.

Knob positions are not saved with the project; the host's parameters are.
All-notes-off (CC 123) and all-sound-off (CC 120) release everything.

## The other two sounds

The prototype's other two sounds, set by hand:

| Setting | Pad | Lead |
|---|---|---|
| Mode | Poly | Mono |
| Oscillator 1 | Saw, -8 cents, 0.45 | Saw, -6 cents, 0.5 |
| Oscillator 2 | Saw, +8 cents, 0.45 | Square, +6 cents, 0.35, width 0.4 |
| Oscillator 3 | Square, octave -1, 0.2 | level 0 |
| Drive | 0.05 | 1.6 |
| Cutoff, resonance, slope | 550 Hz, 0.9, 24 dB | 1400 Hz, 2.0, 24 dB |
| Env amount, key tracking | 1.5 oct, 0.4 | 1.5 oct, 0.6 |
| Filter envelope (A, D, S, R) | 1.5, 2.5, 0.4, 3.0 | 0.04, 0.9, 0.45, 0.6 |
| Amp envelope (A, D, S, R) | 0.9, 1.0, 0.85, 2.5 | 0.012, 0.6, 0.85, 0.35 |
| Glide | | 0.11 s |
| Chorus mix | 0.7 | 0.5 |

## Limits

- No custom interface yet: hosts show their generic parameter panel.
- Tested in a minimal CLAP host, not yet in a DAW. The VST3 exports are present,
  but no VST3 host has loaded it yet.
- Switching waveform, filter slope or mode while a note sounds is a hard switch
  and can click. Every continuous control is smoothed.
- There is no built-in preset browser. The table above sets the pad and lead.
