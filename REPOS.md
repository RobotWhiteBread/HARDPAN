# HARDPAN: repositories and references

Verified 2026-11-11. Check each repo's own README before relying on it,
since activity status changes.

## Important status note on the Rust framework

`nih-plug` is the obvious starting point and still the most widely used
Rust plugin framework, but as of this writing the upstream repository
declares itself to be in **maintenance mode** and points framework users to a
community fork. Read the upstream README first and decide which one you want
before you write any code, because switching later is annoying.

- https://github.com/robbert-vdh/nih-plug (upstream, maintenance mode)
- https://codeberg.org/BillyDM/nih-plug (community fork named in the upstream
  README as the active successor for framework development)
- `nice-plug` on docs.rs is a further fork describing itself as the
  community-led continuation. Verify which of these is actually alive before
  committing.

## Plugin frameworks

| Repo | What it gives you |
|---|---|
| https://github.com/robbert-vdh/nih-plug-template | Cookiecutter template, fastest path to a building plugin |
| https://github.com/free-audio/clap | The CLAP plugin standard itself, worth reading |
| https://github.com/juce-framework/JUCE | The C++ incumbent. Use if you would rather fight C++ than Rust immaturity |

## Rust audio building blocks

| Repo | What it gives you |
|---|---|
| https://github.com/RustAudio/cpal | Cross-platform audio I/O, for a standalone binary |
| https://github.com/RustAudio/dasp | DSP fundamentals: sample types, frame conversion, interpolation |

## Python prototyping

Fastest loop for getting the math right before fighting a plugin format:
`numpy`, `scipy.signal`, `sounddevice`, `soundfile`, `mido` for MIDI (with
`python-rtmidi` as its backend), and `matplotlib` for analysis figures only.

## Real synths worth reading as source

| Repo | Why read it |
|---|---|
| https://github.com/surge-synthesizer/surge | Large, mature, production C++ synth. Read its filter and oscillator code |
| https://github.com/mtytel/vital | Spectral warping wavetable synth, well-regarded architecture |

## Reference material, not code

- `iquilezles.org` and `musicdsp.org` for filter and oscillator formulas
- Search terms that matter: "PolyBLEP", "anti-aliased oscillator",
  "biquad cookbook", "zero-delay feedback filter", "denormal protection"
- The specific thing to look up on day one is **PolyBLEP**, the practical
  solution to sawtooth and square aliasing

## Do not start by cloning a synth

Reading production synth code is useful. Starting from a fork of one defeats
the entire purpose of this project, which is understanding the arithmetic.
