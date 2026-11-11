# HARDPAN: controllers and buttons

Covering three tracks you picked: a pad grid to play it, building your own
controller hardware, and a browser UI with on-screen pads.

They are not three competing options. They are the right order to do them in.

---

## Why this order

**Web UI first.** Costs nothing, needs no shipping, and it is how you find out
what controls you actually want. Every custom controller built before the
builder knew their own workflow has a knob nobody turns.

**Then buy a cheap pad grid.** Fifteen minutes of playing a real grid teaches
you more about pad layout than a week of thinking. Treat the purchase as
research for the build.

**Then build your own.** Now you know how many pads, how many encoders, and
where they go, because you have been annoyed by a layout that was almost
right.

---

## Track 1: a pad grid, off the shelf

### What a grid is good for that a keyboard is not

A piano keyboard is laid out for ten fingers and twelve semitones. A grid is
laid out for rhythm and for state. The things it does better:

- **Step sequencing.** One row is a bar, one column is a step. You see the
  whole pattern at once instead of hearing it go by.
- **Chord and clip triggering.** One pad, one whole chord or phrase.
- **Scale locking.** This is the big one. Map the grid so that every pad is a
  degree of a chosen scale and there is no wrong note on the instrument.
  Isomorphic layouts, where the same interval is always the same physical
  distance, mean a shape you learn in one key works in all of them.
- **Visual state.** RGB pads show you what is armed, playing, or muted.

### Examples, roughly $100 to $200

| Controller | Notes |
|---|---|
| Novation Launchpad Mini / X | 64 RGB pads, the most common choice, enormous amount of community code |
| Akai APC mini | Similar grid plus faders |
| Arturia BeatStep | 16 larger pads plus 16 encoders. Fewer pads, far more knobs |
| Keith McMillen QuNeo | Pressure and position sensitive pads. More expressive, more money |
| Monome Grid | Beautiful, open, and expensive. The thing you build toward, not start with |

**Velocity matters when choosing.** Most cheap grids send velocity; some send
a fixed value. Check before buying, because a pad with no velocity is a
switch and you will feel the difference immediately.

### The idea worth building

Tie the grid's scale lock to the guitar input from `GUITAR_RIG.md`. Detect
the key you are playing in on guitar, and relock the pads to it in real time.
You put the guitar down, hit any pad, and it is in key with what you just
played. That is an instrument behavior that does not exist off the shelf.

A second one: with your pedals captured as neural models, let each pad select
a different **pedal chain** rather than a different note. Stomp a whole
signal path with one finger.

---

## Track 2: build the controller yourself

### Pick the microcontroller for native USB MIDI

This is the one decision that determines how pleasant the project is.

| Board | Why |
|---|---|
| **Teensy 4.x** | Native USB device support, and `usbMIDI` is built into Teensyduino. The board appears as a class-compliant MIDI device with no drivers and no configuration. This is the path of least resistance and what most DIY MIDI controllers use |
| **Raspberry Pi Pico** | Cheaper. USB MIDI via TinyUSB, or CircuitPython with `adafruit_midi`. More assembly required |
| **Daisy Seed** | You may already be buying one for the pedal. It has USB too, so one board could be both synth and controller. Tempting, and more coupled than you want on a first build |

Avoid anything that needs a serial-to-MIDI bridge running on the computer.
Class-compliant is the whole point.

### The electronics you cannot skip

**Matrix scanning.** Sixteen buttons wired individually need sixteen pins.
Wired as a 4x4 matrix they need eight. Scan rows and columns in a loop.

**Diodes, one per switch.** Without them, three keys held down create a
phantom fourth through a sneak path in the matrix. This is called ghosting,
and adding diodes later means desoldering everything. Put 1N4148 diodes in
from the start.

**Debouncing.** A mechanical switch does not close cleanly; it bounces for a
few milliseconds. Debounce in firmware with a short per-key lockout, not with
a global delay that adds latency to everything.

**Velocity is the hard part.** A plain switch is binary. Real options:
- **FSR (force-sensitive resistor) pads**, read on an analog pin. Simple,
  genuinely velocity sensitive, slightly mushy feel.
- **Piezo discs** with an envelope detector, which is how electronic drum
  triggers work. Best dynamics, needs a protection circuit and careful
  thresholding or it double-triggers.
- **Two-contact switches**, measuring the time between the first and second
  contact closing. This is how weighted MIDI keybeds do it. Most work.
- **Accept fixed velocity** and put your expression on an encoder or
  expression pedal instead. Legitimate, and much faster to finish.

**Encoders, not potentiometers,** for anything that selects or scrolls.
Encoders are relative, so they never jump when you change presets. Use
quadrature decoding with interrupts, not polling, or you will drop steps
when you spin them fast.

### Switches and feel

- **Cherry MX style** keyboard switches: cheap, enormous variety, crisp. Not
  velocity capable without the two-contact trick.
- **Arcade buttons**: satisfying, large, loud, very durable. Good for
  footswitches and transport controls.
- **Silicone pads** over FSRs or a membrane: the familiar drum pad feel, the
  hardest to source well as a one-off.

### Latency expectations

USB MIDI round trip is on the order of 1 to 3 milliseconds. Not your problem.
If your controller feels laggy, it is your scanning loop or your debounce
window, not USB.

---

## Track 3: a browser UI with on-screen pads

### Two quite different architectures

**A. Browser as the control surface only.** The synth stays in Python or Rust;
the page is just knobs and pads sending messages to it over a WebSocket. Easy,
and the audio path is unaffected. Best first version.

**B. Browser as the whole instrument.** Port the synth to Web Audio and run
it in the page. Then it works anywhere, needs no install, and you can send
someone a link.

For B, one rule above all: **the DSP must run in an AudioWorklet.** That is
the API that executes on the audio rendering thread. The older
`ScriptProcessorNode` runs on the main thread, glitches whenever the UI is
busy, and is deprecated. Starting with ScriptProcessorNode is the single most
common way this project ends in frustration.

### Web MIDI, so your hardware and your page talk

The Web MIDI API lets the page read your pad grid directly, which means the
same UI works with and without hardware. Two practical notes: it requires a
secure context, so `localhost` or HTTPS, and browser support is uneven, with
Chrome the reliable target and Safari historically limited. Check current
support rather than assuming.

### What the on-screen version is genuinely better at

Not playing. Configuring. A browser UI is the right place for the routing
matrix from `GUITAR_RIG.md`: dragging your captured pedals into a chain,
switching the model between the guitar path and the oscillator path, and
seeing the signal flow. That is a terrible thing to drive from a grid of
buttons and a natural thing to do with a mouse.

---

## Build prompts

### Phase C1: browser control surface

```
My HARDPAN synth prototype runs in Python. Build me a browser control surface
for it.

Architecture: a small Python WebSocket server alongside my audio callback,
and a single self-contained HTML page with no build step and no framework.

The page needs: a 4x4 pad grid, knobs for filter cutoff, resonance, and the
four ADSR parameters, and a visual meter showing the envelope follower value
coming back from the synth.

Requirements:
- Parameter changes must be smoothed in the synth, not applied instantly, or
  they will click. Do the smoothing on the audio side, not in the UI
- Bidirectional: the page reflects state the synth changes on its own
- Pads must respond to mouse, touch, and keyboard
- If the socket drops, the synth keeps running and the page says so

Show me the message protocol before you write either side.
```

### Phase C2: Web MIDI and scale locking

```
Add Web MIDI to my control surface so a connected pad grid drives the same
parameters as the on-screen controls.

Then implement scale locking: a key and scale selector, with the grid mapped
so every pad is a scale degree. Implement an isomorphic layout where the same
interval is always the same physical offset, and explain the layout you chose
and what it is good for.

Light the hardware pads to show the mapping if the device supports it, and
degrade gracefully if it does not.

Finally, wire the key selector to accept the detected key from my guitar
pitch tracker, so the grid relocks to whatever I am playing.
```

### Phase C3: design the hardware before buying anything

```
I want to build my own USB MIDI pad controller around a Teensy 4.x. Before
any parts are ordered, help me design it.

Interview me first: how many pads, how many encoders, whether I need
velocity, whether it needs footswitches, and whether it sits on a desk or on
the floor.

Then produce: a parts list with quantities and rough costs, a wiring diagram
for the switch matrix including diode orientation and why it is that way, the
pin assignment, and an honest assessment of which parts of the build will be
hardest given that I solder but have not built a matrix before.

Tell me what to prototype on a breadboard before committing to anything
permanent.
```

### Phase C4: firmware

```
Write Teensy firmware for the controller we designed.

Must include: matrix scanning with per-key debouncing that does not add
global latency, interrupt-driven quadrature decoding for the encoders, and
class-compliant USB MIDI output using usbMIDI.

Velocity handling per the design decision we made. If we are using FSRs,
include the analog read, the curve mapping from raw reading to MIDI velocity,
and a calibration routine, because the raw range will not match the spec
sheet.

Add a diagnostic mode that prints every key event with timing over serial, so
I can verify there is no ghosting or double-triggering before I trust it.
```
