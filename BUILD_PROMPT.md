# HARDPAN: build prompts

Paste these into a coding agent one phase at a time. Do not paste phase 2
until phase 1 runs and sounds correct.

---

## Phase 1: Python monosynth prototype

```
I am building a software synthesizer from first principles to learn DSP. I do
not want you to use any existing synthesis library, framework, or DSP package
beyond numpy, scipy.signal, sounddevice, soundfile, and mido. Write the
synthesis math yourself.

Build a single-file Python prototype, hardpan_proto.py, with:

1. A sample-accurate sine oscillator class. Phase accumulator, not np.sin over
   a time array, because later phases need per-sample modulation.
2. An ADSR envelope generator, also sample-accurate, with configurable attack,
   decay, sustain, and release in seconds. It must never produce a
   discontinuity at note on or note off.
3. A render function that takes a list of (note_number, start_time,
   duration) tuples and renders a mono float32 buffer at 48kHz.
4. A main block that renders a short C major arpeggio to hardpan_proto.wav
   and also plays it through sounddevice.

Requirements:
- No clicks. If I hear a click at any note boundary, the envelope is wrong.
- Everything in float32, normalized to a peak of 0.8 before writing.
- Comment the reason behind each piece of math, not what the line does. I want
  to understand why a phase accumulator wraps rather than what % does.
- Add a short docstring at the top listing what is intentionally not
  implemented yet.

Do not add a GUI, a CLI framework, a config file, or a package structure. One
file.
```

---

## Phase 1b: play it live

This is what finishes README milestone 1 ("responds to MIDI note on/off").
Phase 1 only renders a fixed list of notes.

```
Extend hardpan_proto.py so I can play it live. Keep it one file, and keep the
offline render and the arpeggio working.

1. Run the existing oscillator and envelope inside a sounddevice output stream
   callback at 48kHz. Do not rewrite the DSP; the callback asks it for one
   block at a time.
2. MIDI input through mido. Treat note-on with velocity 0 as note-off, since
   many keyboards send that instead of a real note-off. Keep the existing
   overlapping-note behaviour: last-note priority, falling back to a key that
   is still held.
3. MIDI arrives on its own thread. Hand events to the audio callback through a
   queue the callback reads without ever waiting (Python cannot be truly
   lock-free, so at minimum never block). Inside the callback: no blocking,
   no printing, no file or port access, and as little allocation as Python
   allows. Live events land at the start of the next block; tell me what
   timing jitter that costs at the block size you pick.
4. I do not own a MIDI keyboard yet. When no MIDI input port exists, open a
   minimal tkinter window that turns two rows of computer keys into a piano.
   It is there only because a terminal cannot report key releases, and
   note-off needs them. Windows auto-repeats a held key as repeated presses;
   ignore the repeats.
5. On Windows, prefer the WASAPI output over the default MME device, which adds
   around 100ms. Also support ASIO for when I buy an interface: sounddevice
   only exposes ASIO when the environment variable SD_ENABLE_ASIO is set
   before sounddevice is imported.
6. Count buffer underruns. When I quit, release any held note and let the
   release finish before closing the stream, so quitting does not click either.

Same rules as phase 1 otherwise: no CLI framework, no config file, no package
structure. The tkinter window is the only exception to "no GUI".

Then tell me the output latency you measured, the smallest block size that
runs without underruns on my machine, and what fraction of each block's time
budget the callback uses.
```

---

## Phase 2: oscillators that do not alias

```
Extend hardpan_proto.py with sawtooth and square oscillators.

Implement them twice, as separate classes, so I can compare:
1. The naive versions, computed directly from the phase accumulator.
2. PolyBLEP-corrected versions.

Then add a comparison script that renders a rising sweep from 100Hz to 8kHz
with each implementation and writes a matplotlib figure of their spectrograms
side by side. matplotlib is allowed for the figure only. Compute the
spectrogram yourself with numpy's FFT over short windowed frames, and explain
why the window matters. In a sweep, aliased partials show up as lines that
bend back down while the real harmonics keep rising; a single spectrum of the
whole sweep would smear them together.

PolyBLEP reduces aliasing, it does not remove it. Show me what is left, and
where it is worst.

Explain, in comments, what a BLEP actually is and why the correction is
applied only within one sample of a discontinuity.
```

---

## Phase 3: filter and polyphony

```
Add to the prototype:

1. A biquad filter class implementing a resonant low-pass, with coefficients
   derived from the standard audio EQ cookbook formulas. Implement the
   coefficient math explicitly; do not call scipy.signal.butter. Also offer a
   24 dB/octave mode built from two biquads in series, and tell me what
   cascading does to the resonance.
2. Denormal protection in the filter state, and a comment explaining what
   denormals are and why they matter in a real-time context.
3. A voice allocator supporting 8 simultaneous voices. A new note takes a free
   voice if there is one. When all 8 are busy, steal one: prefer the voice that
   has been releasing longest, otherwise the oldest held note. Never cut it
   hard, but do not just give it a normal release either, because then it
   stays busy for the whole release and the new note still has nowhere to go.
   Fade the stolen voice to silence over a few milliseconds, then start the
   new note on it. Tell me the fade length you chose and what it does to the
   new note's timing.
4. A second ADSR routed to the filter cutoff, so the filter envelope is
   independent of the amplitude envelope.
5. Let each voice mix up to three oscillators, so a patch can stack two saws
   detuned a few cents apart with a square sub-oscillator an octave down.
   Explain why a few cents of detune sounds thicker, and what sets the rate
   of the beating.
6. Two presets that actually sound good, not just ones that exercise the
   features, aimed at the README's sound target. Tell me why each value.
   - A bass: the detuned saws plus the sub, the low-pass mostly closed, and a
     filter envelope with an instant attack and a short decay, so every note
     snaps.
   - A pad: slow attack, long release, the filter darker still.

Then render two things: the bass playing a 16th-note sequence in a minor key
at about 110 BPM, and the pad playing a slow minor chord progression that
includes a passage with more than 8 overlapping notes, so the stealing is
audible. Tell me which parameter values make the filter self-oscillate so I
know where the resonance limit is.
```

---

## Phase 3b: the 80s toolkit

Everything that turns the phase 3 voices into the README's sound target. It
is still Python and still rendered offline: this much per-sample Python will
not keep up in real time, which is what phase 4 is for.

```
Add to the prototype, with all the DSP written by hand:

1. Drive: a tanh waveshaper with an input gain, between the oscillators and
   the filter. A nonlinearity creates new harmonics, and those alias like any
   others, so run it oversampled (2x or 4x) with a half-band filter you design
   yourself. Show me its aliasing with and without oversampling, the same way
   phase 2 compared the oscillators.
2. Glide for the mono bass. The pitch slides exponentially (equal time per
   semitone), not linearly in Hz, with the time in milliseconds. Explain why.
3. A step sequencer: tempo in BPM, 16th-note steps, a note, gate length, and
   accent per step, plus swing. Accent should hit both the amplitude and the
   filter envelope, the way it did on 80s sequenced bass.
4. A chorus in the style of the Juno: two delay lines swept by slow LFOs in
   opposite phase. Explain why the delay's read position has to be
   interpolated between samples.
5. A tempo-synced delay with feedback, a low-pass inside the feedback loop so
   the repeats get darker, and a limiter so the feedback cannot run away.
6. A reverb from Schroeder or Moorer style combs and all-passes, with damping
   in the combs for a dark tail. Explain why the comb delay lengths are
   chosen to share no common factors.

Then render a 30-second demo in a minor key at about 110 BPM: the sequenced
bass, the pad, and a mono lead with glide, all through the effects.
```

---

## Phase 4: port to a plugin

```
Read https://github.com/robbert-vdh/nih-plug and its README note about
maintenance status, then tell me which of nih-plug, the BillyDM community
fork, or nice-plug is currently the best target for a new project. Give me
your reasoning and the evidence, and do not pick one without checking current
repository activity.

Once I choose, scaffold a Rust plugin from the appropriate template that
ports exactly what my Python prototype does: sine, PolyBLEP saw, PolyBLEP
square, amplitude ADSR, filter ADSR, resonant biquad, 8-voice polyphony, and
the phase 3b drive, glide, sequencer, chorus, delay, and reverb.

Constraints that matter more than features:
- No allocation in the process callback. None. Preallocate all voice state.
- No locks in the audio thread.
- Parameter smoothing on anything a user can turn, or it will click.
- Keep the DSP in its own crate, separate from the plugin wrapper, so the math
  is testable without a host.
- Make that DSP crate no_std (use the libm crate for sin, exp and the like;
  tests may still use std). The same crate will later run on a Daisy Seed
  pedal, so it cannot depend on the standard library or an allocator. The
  plugin wrapper can use std freely.

Write unit tests for the oscillator and envelope that assert on sample values,
not just that the code runs.
```

---

## Verification prompt, use after every phase

```
Review the DSP math in this file as a critical reviewer who writes audio
software. Specifically check: phase wrapping correctness, envelope
discontinuities, filter stability at extreme cutoff and resonance values,
off-by-one errors in buffer indexing, and anything that would allocate or
block if this ran in a real-time audio callback. Tell me what is wrong before
telling me what is right.
```
