# HARDPAN as a guitar instrument

Design notes for making HARDPAN something you plug into an amp, play
alongside your guitars, and load your own pedals into. Verified 2026-11-11.

This changes the project from "a synth I built" into "an instrument that
only I have," which is a much better project.

---

## 1. Getting it into the amp

### Bluetooth from the computer to a speaker

This is the normal, easy route, and it works fine for *listening*. It does not
work for *playing*. The distinction matters more than it sounds.

Standard Bluetooth audio (A2DP) adds roughly 150 to 200 milliseconds between
the computer deciding on a sample and the speaker producing it. Low-latency
codecs can get that to around 30 to 40ms in good conditions, if both ends
support the same one. That delay sits on top of whatever your audio buffer
already costs.

So:

| What you are doing | Bluetooth speaker |
|---|---|
| Listening back to a render or a loop | Fine. Use it |
| Sequencing a pattern and tweaking knobs while it loops | Usable, slightly annoying |
| Playing notes live from a keyboard | Bad. Every note arrives late |
| Playing along with your guitar | Unusable |

The reason the last row is absolute: you hear your guitar acoustically and
through the amp at effectively zero delay, while the synth arrives a sixth of
a second later. You cannot play against that, and no amount of practice
adapts to it because the two sounds are not offset by a constant you can
anticipate; Bluetooth latency drifts.

### What to get instead

A USB audio interface, which solves the problem from both directions at once.
It is how the guitar gets *into* the computer in the first place, and its
outputs are wired, so they are also how sound gets back out with single-digit
millisecond latency. On Windows, get one with a real ASIO driver; the generic
Windows audio path will not give you low latency even over a cable.

That one purchase unlocks the guitar input path, the low-latency output, and
the pedal capture rig in section 4, which needs simultaneous in and out
anyway. Keep the Bluetooth speaker for listening back to what you made.

### Wired, into a guitar amp: three options

**Option A, the amp's FX return.** A synth puts out line level; a guitar amp
input expects instrument level from a high-impedance pickup. Going into the
front of the amp will overdrive the preamp whether you want that or not.
Plugging into the effects return (sometimes labeled power amp in) bypasses the
preamp and treats your amp as a power amp and speaker. Cleanest result.

**Option B, a reamp box.** A reamp box converts line level back down to
instrument level and impedance, so your synth sees the amp exactly like a
guitar does. This is the option to pick if you *want* the preamp grit and
want the synth to sit in the same tonal world as your guitar.

**Option C, make HARDPAN a pedal.** See below. This is the real answer.

### Two warnings about guitar speakers

A guitar speaker is a deliberately band-limited device. A 12" guitar speaker
has no tweeter and rolls off steeply in the treble, so the top end of your
synth will simply not be reproduced. Accept that as part of the instrument's
voice rather than fighting it.

More seriously: guitar speakers are not designed for sustained low-frequency
energy. A synth bass patch at volume into a guitar cab can damage the
speaker in a way a guitar never would. High-pass your output and keep the
sub content out of the amp.

---

## 2. Build it as a pedal

This is the single best decision available for what you described, and it is
cheap. The Daisy Seed by Electro-Smith is a Cortex-M7 embedded audio platform
with its own open-source hardware library (libDaisy) and DSP library
(DaisySP), programmed in C++. Paired with a pedal interface board it becomes
a stompbox with 1/4" jacks, knobs, footswitches, and 9V power, which means
HARDPAN sits on your board between the pedals you already own.

Interface board options:

| Board | Notes |
|---|---|
| PedalPCB Terrarium | The common choice. Mono, up to 6 knobs, 4 switches, 2 footswitches. Around $12 for the PCB |
| Cleveland Music Hothouse | A DIY kit aimed at exactly this, sold as a kit rather than a bare PCB |
| GuitarML FunBox | Stereo platform, KiCad files open on GitHub |

GuitarML reports total material cost for a complete Daisy Seed plus
Terrarium digital multi-effect at under $100. Note one gotcha from their
repo: Daisy Seed revision 7 and later changed audio codec and may need
additional components for the Terrarium, so check revision compatibility
before ordering.

Running your DSP on hardware forces discipline that a plugin never will. You
get a fixed CPU budget, no allocation, and a real-time deadline you cannot
negotiate with. That is the good kind of constraint.

---

## 3. Making it respond to your playing

Four approaches, ordered by how soon you will have something fun working.
Start at the top.

### A. Envelope follower (start here)

Rectify and smooth the guitar signal to get its amplitude envelope, then use
that to drive synth parameters: filter cutoff, oscillator mix, resonance,
anything. This is the mechanism inside every auto-wah and envelope filter.

Why start here: it is a handful of lines of DSP, the latency is essentially
zero because you are not trying to identify anything, and it feels
responsive immediately because it tracks your right hand. Pick harder, the
filter opens. No pitch detection required.

### B. Guitar as the oscillator

Instead of synthesizing a pitch and trying to match your guitar, use the
guitar signal itself as the raw waveform and run it through HARDPAN's filter,
envelopes, and modulation. The guitar becomes the oscillator and your synth
becomes everything after it.

This is how the classic guitar synth pedals work, and it has two large
advantages: it is always perfectly in tune with itself, and there is no
tracking latency at all because nothing is being tracked. Combined with the
envelope follower above, you have a genuinely expressive instrument before
you have written a single pitch detector.

### C. Monophonic pitch tracking

Now you are fighting physics. A low E on a guitar is about 82Hz, so one cycle
takes roughly 12ms. Reliable pitch detection needs two or three cycles, which
puts you at 25 to 40ms of unavoidable latency on the low strings. This is
exactly why every guitar-to-MIDI product ever made feels slightly late, and
no amount of good code fixes it.

Two mitigations worth knowing. First, latency scales inversely with
frequency, so tracking is much faster on the high strings; a patch designed
for lead playing above the 7th fret will feel far better than one that has to
handle open low E. Second, and more clever: detect the *transient* first and
fire the note immediately with a provisional pitch, then correct the pitch a
few milliseconds later once you have enough signal to be confident. The
attack arrives on time and the pitch snaps into place inside the attack
envelope, where the ear is least sensitive to it.

Algorithms to look at: YIN and pYIN for robustness, plain autocorrelation to
understand the principle first.

### D. Hexaphonic pickup

A divided pickup gives you one signal per string, which is the only honest
way to get polyphonic tracking. Each string is monophonic, so you run six
independent trackers and the whole problem becomes tractable. This is how the
serious guitar synth systems do it. It requires mounting a divided pickup on
a guitar, so treat it as a later phase, and probably not on a guitar you love.

### E. The fun one: sympathetic strings

Run a bank of Karplus-Strong resonators tuned to your guitar's open strings,
excited by whatever you play. Play an E anywhere on the neck and the low E
resonator starts ringing, the way it does acoustically but much more so.
Ties directly into the plucked-string stretch goal already in the README.

---

## 4. Cloning your pedals

Short answer: yes for some of them, no for others, and the dividing line is
sharp and worth understanding.

### What captures well

Neural capture of analog gear is a mature, open-source, free thing now. You
play a known test signal through the pedal, record the output, and train a
small neural network on the input/output pair. The result runs in real time.

Two ecosystems:

**Neural Amp Modeler (NAM)** by Steven Atkinson. Python package for training,
`.nam` output format, a partner plugin for real-time playback, and a Colab
notebook so you do not need a local GPU.
- https://github.com/sdatkinson/neural-amp-modeler
- https://github.com/sdatkinson/NeuralAmpModelerPlugin

**GuitarML** by Keith Bloemer. Proteus is the current plugin (LSTM, hidden
size 40) with a capture utility and Colab script. Importantly, it can train
*conditioned* models, meaning the model learns how the pedal responds across
a knob's full range rather than freezing one setting.
- https://github.com/GuitarML/Proteus
- https://github.com/GuitarML/Automated-GuitarAmpModelling (the trainer)
- https://github.com/GuitarML/NeuralSeed (runs captures on Daisy Seed)
- https://github.com/GuitarML/NeuralPi (Raspberry Pi version)

### What does not capture

Straight from the Proteus repo, this technique is effective for amps,
preamps, and distortion/overdrive/boost pedals, and explicitly not for
time-based effects: no reverb, no delay, no flanger, no phaser.

The reason is structural, not a limitation of the training. These models learn
a nonlinear input-to-output function. A delay or a chorus has internal state
and a running LFO, so its output depends on what happened hundreds of
milliseconds ago and on where its oscillator happens to be. A static
nonlinear function cannot represent that.

Also, modulation and time effects must be *off* during capture of anything
else, or they corrupt the training data. Same for noise gates.

So the plan splits cleanly:
- **Dirt, fuzz, overdrive, boost, compressor, amp preamp**: capture them.
- **Delay, reverb, chorus, flanger, phaser, tremolo**: build them from
  scratch in HARDPAN. Which is the DSP education you wanted anyway, and these
  are the most rewarding effects to write.

### What the capture rig needs

An audio interface with simultaneous in and out, so you can send the training
WAV through the pedal and record the result in one pass. Then align the two
files sample-accurately (the round trip introduces a delay you must remove)
and train. Both ecosystems supply a standard input file and a Colab notebook.

### Expectation setting on the embedded version

NeuralSeed runs on the Daisy Seed, but only a GRU of hidden size 10, against
Proteus's LSTM of size 40. Its own README is refreshingly honest: captures at
that size are hit or miss, may need massaging, and may not work at all;
training loss above roughly 0.08 probably will not sound good. It holds
around 15 models in 128KB of flash.

Practical reading: capture at full size for the plugin version, and treat the
pedal version as a distilled subset of your best few models.

One note, not legal advice: capturing your own gear for your own use is
uncontroversial, and the NAM community shares captures openly. Distributing
models of commercial products is murkier ground. Keep your captures private
and the question never comes up.

---

## 5. What makes this genuinely unique

Any of the above gets you a nice guitar synth. These are the ideas that make
it an instrument nobody else has.

### Your pedals as synth modules

This is the best idea on the list. Once your fuzz exists as a neural model,
run the *synth's own oscillator* through it. Not the guitar. The synth.

Your Big Muff was designed to be fed a guitar pickup, and almost nobody has
ever fed it a raw PolyBLEP sawtooth at full scale. The nonlinearity will do
things to a synth waveform that no stock waveshaper in any soft synth does,
because it is a model of a specific, physical, slightly-out-of-spec circuit
that you own. Your synth ends up sounding like your pedalboard, and that is
a timbre that exists nowhere else.

### Impossible pedal chains

A physical pedalboard is a fixed series chain. Software is not. With your
pedals captured you can:
- Run two drives in **parallel** and blend them, instead of stacked
- Split by frequency band: fuzz on the low strings only, clean highs
- Put a drive **inside a feedback loop** with a delay
- Reorder the entire chain instantly, including orders that would require
  repatching six cables
- Put a pedal **after** the synth filter instead of before it

None of this requires new DSP. It is routing. And it is the thing you
physically cannot do on the floor.

### A few more

- **Footswitch control of synth parameters.** Your hands are busy. An
  expression pedal driving filter cutoff or oscillator detune gives you a
  third limb.
- **Looper into randomized chains.** Record a phrase, play it back through a
  shuffled pedal order every repeat.
- **Guitar triggers, synth answers.** Detect what you played, have the synth
  respond in the same key rather than in unison.

---

## 6. Build prompts for this track

Paste one phase at a time. These assume Phase 1 to 3 of `BUILD_PROMPT.md`
are done, since they all need the filter and envelope.

### Phase G1: listen to the guitar

```
Extend my HARDPAN Python prototype with a real-time guitar input path using
sounddevice's duplex callback at 48kHz.

Implement, with the math written out rather than pulled from a library:
1. An envelope follower: full-wave rectify, then a one-pole smoother with
   separate attack and release coefficients. Expose attack and release in
   milliseconds and convert to coefficients correctly for the sample rate.
2. A routing so the envelope drives my existing filter's cutoff, with
   adjustable depth and the ability to invert it.
3. A "guitar as oscillator" mode where the input signal replaces the
   oscillator entirely and passes through my filter and amplitude envelope.
4. A DC blocker on the input and a high-pass on the output, with a comment on
   why both are necessary.

Report the measured round-trip latency and tell me what buffer size I need
for it to stay under 10ms. Do not add pitch detection in this phase.
```

### Phase G2: pitch tracking, honestly

```
Add monophonic pitch tracking to the prototype.

Implement autocorrelation-based detection first, so I understand the
principle, then YIN, so I can hear the difference in robustness.

Then implement the two-stage trigger: detect the attack transient with an
onset detector and fire the synth note immediately at a provisional pitch,
then refine the pitch once enough samples have arrived, gliding to the
corrected value inside the attack envelope.

Instrument the whole thing: for each detected note, log the onset time, the
time the pitch became confident, and the error of the provisional guess. I
want to see the latency, not be told it is fine.

Tell me honestly where this falls apart: which strings, which playing
styles, double stops, bends, palm mutes.
```

### Phase G3: capture a pedal

```
Walk me through capturing one overdrive pedal with Neural Amp Modeler,
starting from an audio interface and the pedal on my desk.

I need: the exact signal chain, how to get the standard NAM training file,
how to avoid clipping on the way in and out, how to verify the recording is
sample-aligned before training, what training loss value means a good
capture, and how to A/B the result against the real pedal.

Then tell me what I need to change to capture the same pedal as a
knob-conditioned model with GuitarML's trainer instead, and which of the two
I should prefer for a drive pedal whose gain knob I actually use.
```

### Phase G4: pedals as synth modules

```
Add a neural model inference stage to HARDPAN that can load a trained capture
and process audio through it in real time. Use RTNeural, which is the engine
both GuitarML and the Daisy ports use.

Then build a routing matrix, not a fixed chain, that lets me place the model
anywhere: on the guitar input, on the synth oscillator before the filter, on
the synth output after the filter, in parallel with a dry path, or inside a
feedback loop with a delay line.

Requirements:
- The oscillator path must be able to hit the model at full scale, since the
  point is to drive it harder than a guitar ever would. Give me an input gain
  stage and an output trim so I can do that without destroying my speakers.
- Feedback loops need a limiter and a clear kill switch. Tell me where the
  instability is before I find it at volume.
- Keep inference out of any allocation path.

Start by showing me the routing data structure before you write any DSP.
```
