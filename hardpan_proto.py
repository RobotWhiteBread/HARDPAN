"""HARDPAN phases 1 and 1b: a monophonic sine synth, rendered offline or played live.

One sine oscillator and one ADSR envelope, computed one sample at a time in
float32 at 48 kHz. All of the synthesis math is written out in this file.
numpy holds the samples, soundfile writes the WAV, sounddevice talks to the
sound card, and mido reads MIDI.

    python hardpan_proto.py            render a C major arpeggio to
                                       hardpan_proto.wav and play it
    python hardpan_proto.py live       play live from a MIDI keyboard, or from
                                       the computer keyboard if none is connected
    python hardpan_proto.py live 128   the same, with 128-frame audio blocks

Intentionally not implemented yet:
- Polyphony. There is one voice. A note that starts while another is held takes
  the voice over (last-note priority), and releasing it falls back to a note
  that is still held.
- Velocity (MIDI velocity is read and ignored), pitch bend, glide, sustain pedal.
- Sample-accurate timing for live notes. A live note starts at the beginning of
  the next audio callback, so it can be late by up to the time between
  callbacks (see LiveEngine._callback). The offline render is sample accurate
  because it knows every note in advance.
- Choosing an audio device or MIDI port by hand. Live mode takes ASIO if a
  driver is installed, then WASAPI, then the Windows default, and opens every
  MIDI input present at startup.
- Any waveform other than sine. A sine has no harmonics above its fundamental,
  so it cannot alias, and anti-aliasing waits for saw and square (phase 2).
- Filters (phase 3).
- Smoothing for parameters changed while a note sounds. Parameters stay fixed
  for the length of a render.
- Denormal protection. The release snaps to exactly 0.0 instead of decaying
  forever, so nothing here lingers near zero yet (phase 3).
"""

import math
import os
import sys
import time
from collections import deque
from pathlib import Path

import numpy as np

SAMPLE_RATE = 48000
F32 = np.float32
TWO_PI = F32(2.0 * math.pi)


def midi_to_hz(note):
    # Pitch is heard as ratios, not differences: every octave doubles the
    # frequency, and equal temperament splits that doubling into 12 equal
    # ratios of 2**(1/12). MIDI note 69 is A4, tuned to 440 Hz by convention.
    return F32(440.0 * 2.0 ** ((note - 69) / 12.0))


class SineOscillator:
    """Produces one sample per tick, so the frequency can change on any sample."""

    def __init__(self, sample_rate=SAMPLE_RATE):
        self.sample_rate = F32(sample_rate)
        # Phase is measured in cycles (0 to 1), not radians. A cycle is one trip
        # through the waveform at any frequency, so the per-sample step is a plain
        # fraction and the wrap point is exactly 1.
        self.phase = F32(0.0)
        self.increment = F32(0.0)

    def set_frequency(self, hz):
        # f Hz is f cycles per second, and there are sample_rate samples per
        # second, so each sample advances f / sample_rate of a cycle. At 440 Hz and
        # 48 kHz that is 0.00917 of a cycle per sample, about 109 samples per cycle.
        # Changing frequency only changes the step size. The phase stays where it
        # is, so the waveform continues from its current point instead of jumping.
        self.increment = F32(hz) / self.sample_rate

    def reset(self):
        self.phase = F32(0.0)

    def tick(self):
        # Output before advancing, so a note that starts at phase 0 really
        # starts at sin(0) = 0.
        out = np.sin(TWO_PI * self.phase)
        self.phase += self.increment
        # Why wrap: a sine repeats every cycle, so phase 3.25 and phase 0.25 give
        # the same output. They are not equally precise, though. float32 keeps
        # about 7 significant digits, so the gap between neighbouring values grows
        # as the number grows. Inside [0, 1) the phase resolves about 6e-8 of a
        # cycle. Left to count up for a minute at 440 Hz, it would reach 26,400,
        # where neighbouring float32 values are 0.002 apart. The 0.0092 step would
        # then round to 0.0098 on every sample and the note would play about a
        # semitone sharp. After about ten minutes the step would round to nothing
        # and the oscillator would stop. Wrapping keeps the phase in the range
        # where float32 is finest.
        if self.phase >= 1.0:
            # Subtract a cycle rather than taking a remainder. The phase is between
            # 1 and 2 here, and subtracting 1 from such a number is exact in
            # floating point, so the wrap itself loses nothing. One subtraction is
            # always enough, because the step is under one cycle for any pitch
            # below the sample rate.
            self.phase -= F32(1.0)
        return out


IDLE, ATTACK, DECAY, SUSTAIN, RELEASE = range(5)


class ADSR:
    """Attack, decay, sustain, release, one sample per tick, with no discontinuities.

    A click is two neighbouring samples far apart. A step like that contains
    energy at every frequency, so it is heard as a tick on top of the note.
    Envelopes click when an event resets the level rather than moving it: a
    note-on that restarts the attack from 0 while the last note is still
    sounding, or a note-off during the attack that starts the release from the
    sustain level instead of from wherever the envelope actually is.

    In this envelope, every stage moves from the current level toward a target.
    Note-on and note-off choose a new stage and target, but never touch the
    level, so no event can cause a jump.
    """

    # A stage of zero length is a step, and so is a very short one: 1 ms is only
    # 48 samples. Holding every stage to at least 5 ms (240 samples) keeps each
    # per-sample change under about 4% of full scale, even with times set to 0.
    MIN_STAGE_SECONDS = 0.005

    # Every stage is an exponential approach (see tick), and an exponential
    # approach never quite arrives. So each stage aims past its goal and ends when
    # it crosses the goal, the way an analog envelope charges a capacitor toward
    # a voltage above the threshold that ends the stage. The overshoot, as a
    # fraction of the distance travelled, sets the shape of the curve.
    #
    # Attack aims 30% past full level, so only the early, nearly straight part of
    # the curve is used, which gives a fast and even rise. Decay and release aim
    # 0.0001 past their goals, about -80 dB, so they are almost pure exponential
    # decays. That matters because loudness is heard in decibels, and an
    # exponential falls the same number of decibels in every millisecond, so it
    # sounds like an even fade. A linear fade loses few decibels at first and
    # almost all of them at the end, so it seems to hang and then drop away.
    ATTACK_OVERSHOOT = 0.3
    DECAY_OVERSHOOT = 1e-4

    def __init__(self, attack=0.010, decay=0.150, sustain=0.7, release=0.300, sample_rate=SAMPLE_RATE):
        """Times in seconds. attack: silence to full level. decay: full level to
        sustain. release: full level to silence (quicker from a lower level).
        sustain: a level from 0 to 1."""
        if not 0.0 <= sustain <= 1.0:
            raise ValueError(f"sustain must be between 0 and 1, got {sustain}")
        self.sustain = F32(sustain)
        self.attack_target = F32(1.0 + self.ATTACK_OVERSHOOT)
        # Decay overshoot is measured against the distance it falls, (1 - sustain),
        # so the curve has the same shape whatever the sustain level.
        self.decay_target = F32(sustain - self.DECAY_OVERSHOOT * (1.0 - sustain))
        self.release_target = F32(-self.DECAY_OVERSHOOT)
        self.attack_coef = self._coefficient(attack, self.ATTACK_OVERSHOOT, sample_rate)
        self.decay_coef = self._coefficient(decay, self.DECAY_OVERSHOOT, sample_rate)
        self.release_coef = self._coefficient(release, self.DECAY_OVERSHOOT, sample_rate)
        self.level = F32(0.0)
        self.distance = F32(0.0)  # level minus the current stage's target; see tick
        self.stage = IDLE

    @classmethod
    def _coefficient(cls, seconds, overshoot, sample_rate):
        # Starting at 0 and aiming at 1 + overshoot, the level after n samples is
        # (1 + overshoot) * (1 - c**n). For it to cross 1 after exactly
        # n = seconds * sample_rate samples, c**n must equal
        # overshoot / (1 + overshoot), which gives the expression below.
        # It is computed in double precision and rounded to float32 only once,
        # because for long stages c sits very close to 1, where float32 can only
        # hold (1 - c) to a few digits.
        n = max(seconds, cls.MIN_STAGE_SECONDS) * sample_rate
        return F32(math.exp(math.log(overshoot / (1.0 + overshoot)) / n))

    @property
    def active(self):
        return self.stage != IDLE

    def note_on(self):
        # The level is left alone on purpose. A retrigger during a release rises
        # from wherever the release had reached, rather than dropping to 0 first.
        self._begin(ATTACK, self.attack_target)

    def note_off(self):
        # Again, only the destination changes. A note released halfway through
        # its attack fades from that halfway level, rather than first jumping to
        # sustain.
        if self.stage != IDLE:
            self._begin(RELEASE, self.release_target)

    def _begin(self, stage, target):
        # The envelope keeps track of how far it is from its target (see tick), so
        # starting a stage means measuring the current level against the new
        # target. The level itself does not change.
        self.stage = stage
        self.distance = self.level - target

    def tick(self):
        # Each sample closes the same fraction (1 - c) of the distance between the
        # level and the target. That is a one-pole lowpass filter, and its step
        # response is an exponential curve.
        #
        # Why keep the distance rather than the level: near the end of a decay
        # toward 0.7, each step is about 4e-8, which is less than the 6e-8 between
        # neighbouring float32 values near 0.7. Added to the level, a step that
        # small rounds to a whole spacing or to nothing. For any decay longer than
        # about 0.2 s it rounds to nothing, and the decay stalls just above
        # sustain and never ends. The distance is a small number, and float32
        # values are packed far more closely near zero, so multiplying it by c
        # keeps shrinking it smoothly for as long as the stage runs. The level is
        # rebuilt from it every sample.
        #
        # When a stage crosses its goal, the level is set to exactly the goal. The
        # crossing sample has overshot by less than one step, so snapping it back
        # makes that step smaller, not larger. It also means the next stage starts
        # from an exact value: sustain holds exactly the sustain level, and the
        # release ends at exactly 0.0 rather than at a tiny leftover.
        if self.stage == ATTACK:
            self.distance *= self.attack_coef
            self.level = self.attack_target + self.distance
            if self.level >= 1.0:
                self.level = F32(1.0)
                self._begin(DECAY, self.decay_target)
        elif self.stage == DECAY:
            self.distance *= self.decay_coef
            self.level = self.decay_target + self.distance
            if self.level <= self.sustain:
                self.level = self.sustain
                self.stage = SUSTAIN
        elif self.stage == RELEASE:
            self.distance *= self.release_coef
            self.level = self.release_target + self.distance
            if self.level <= 0.0:
                self.level = F32(0.0)
                self.stage = IDLE
        return self.level


class MonoVoice:
    """One oscillator and one envelope, plus the decision about which note sounds.

    There is one voice, with last-note priority. The held list remembers which
    keys are still down. Without it, playing legato (B starts before A ends) and
    then ending A would silence B, because A's note-off would release the voice.
    """

    def __init__(self, attack=0.010, decay=0.150, sustain=0.7, release=0.300):
        self.osc = SineOscillator()
        self.env = ADSR(attack, decay, sustain, release)
        self.held = []  # notes currently down, most recent last

    @property
    def active(self):
        return self.env.active

    def note_on(self, note):
        if note in self.held:
            self.held.remove(note)
        self.held.append(note)
        if not self.env.active:
            # A silent voice has nothing to stay continuous with, so restart the
            # phase. Each note then begins at sin(0) = 0 and every attack sounds
            # alike. While the voice is sounding, the phase is never reset: that
            # would jump the waveform mid-cycle, which is a click.
            self.osc.reset()
        self.osc.set_frequency(midi_to_hz(note))
        self.env.note_on()

    def note_off(self, note):
        if note not in self.held:
            return
        was_sounding = self.held[-1] == note
        self.held.remove(note)
        if not was_sounding:
            return
        if self.held:
            self.osc.set_frequency(midi_to_hz(self.held[-1]))  # back to the key still held, no retrigger
        else:
            self.env.note_off()

    def all_notes_off(self):
        self.held.clear()
        self.env.note_off()

    def tick(self):
        # The envelope is a gain applied to the oscillator, sample by sample.
        # Multiplying is what amplitude means: an envelope of 0.5 is half the
        # waveform's height, which is 6 dB quieter.
        return self.osc.tick() * self.env.tick()


def render(notes, attack=0.010, decay=0.150, sustain=0.7, release=0.300):
    """Render (note_number, start_time, duration) tuples, with times in seconds,
    to a mono float32 buffer at 48 kHz. The buffer runs until the last release
    has reached silence."""
    voice = MonoVoice(attack, decay, sustain, release)

    # Sample-accurate means each note-on and note-off happens on its own sample,
    # the nearest one to its time, rather than at the start of some block.
    # Note-offs sort before note-ons on the same sample (0 before 1). That way a
    # note repeated back to back is released and then retriggered. In the other
    # order, the new note would be released on the very sample it started.
    events = []
    for note, start, duration in notes:
        on = round(start * SAMPLE_RATE)
        off = max(round((start + duration) * SAMPLE_RATE), on + 1)  # a note always lasts at least a sample
        events.append((on, 1, note))
        events.append((off, 0, note))
    events.sort()

    out = []
    n = 0
    i = 0
    while i < len(events) or voice.active:
        while i < len(events) and events[i][0] == n:
            _, kind, note = events[i]
            voice.note_on(note) if kind else voice.note_off(note)
            i += 1
        out.append(voice.tick())
        n += 1
    return np.array(out, dtype=F32)


# Live playing (phase 1b) ----------------------------------------------------

# Live output has to be scaled before anyone hears it, since there is no
# finished buffer to normalize. 0.8 matches the peak the offline render is
# normalized to, so live and rendered notes play at the same level.
LIVE_GAIN = F32(0.8)

# Two rows of a QWERTY keyboard laid out like piano keys, from C4 (MIDI 60).
KEY_TO_NOTE = {
    "a": 60, "w": 61, "s": 62, "e": 63, "d": 64, "f": 65, "t": 66, "g": 67, "y": 68,
    "h": 69, "u": 70, "j": 71, "k": 72, "o": 73, "l": 74, "p": 75, "semicolon": 76,
    "apostrophe": 77,
}
KEY_LAYOUT = (
    "   W E   T Y U   O P\n"
    "  A S D F G H J K L ; '\n"
    "  C D E F G A B C D E F    from C4\n"
    "\n"
    "Esc or close the window to quit."
)
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(note):
    return f"{NOTE_NAMES[note % 12]}{note // 12 - 1}"


class LiveEngine:
    """Runs a MonoVoice inside the sound card's callback.

    The sound card plays one block while the next is being computed. The
    callback that computes it runs on PortAudio's audio thread and has a hard
    deadline: if the block is not ready when the previous one runs out, the
    card plays a gap instead (an underrun), which is heard as a click. So the
    callback does nothing that can make it wait: no printing, no files, no MIDI
    ports, no locks someone else might be holding.

    Notes come from other threads (mido's, or the keyboard window's) through a
    deque. Appending to one end and popping from the other are each atomic in
    CPython, so neither side needs a lock and the callback never waits for the
    sender. It is not truly lock-free, since every Python thread still has to
    take the interpreter lock to run at all; see run_live for how that wait is
    kept short.
    """

    def __init__(self, sd, blocksize=256):
        self.voice = MonoVoice()
        self.events = deque()
        self.blocksize = blocksize
        self.underruns = 0
        self.blocks = 0
        self.busy_seconds = 0.0
        self.busy_peak = 0.0
        self.stream, self.device_name = self._open(sd)
        self.latency = self.stream.latency

    def _open(self, sd):
        # Windows' default device goes through MME, which adds around 100 ms of
        # latency: enough to feel every note arrive late. ASIO (when a driver is
        # installed) and WASAPI talk to the hardware far more directly.
        candidates = []
        for wanted in ("ASIO", "Windows WASAPI"):
            for api in sd.query_hostapis():
                if api["name"] == wanted and api["default_output_device"] >= 0:
                    candidates.append(api["default_output_device"])
        candidates.append(None)  # the system default, as a last resort
        errors = []
        for device in candidates:
            info = sd.query_devices(device, "output")
            api = sd.query_hostapis(info["hostapi"])["name"]
            # WASAPI shared mode normally insists on the sample rate Windows mixes
            # at. auto_convert lets Windows resample when that is not 48 kHz.
            extra = sd.WasapiSettings(auto_convert=True) if "WASAPI" in api else None
            try:
                stream = sd.OutputStream(
                    samplerate=SAMPLE_RATE,
                    blocksize=self.blocksize,
                    device=device,
                    channels=min(2, info["max_output_channels"]),
                    dtype="float32",
                    latency="low",
                    extra_settings=extra,
                    callback=self._callback,
                )
            except sd.PortAudioError as error:
                errors.append(f"{info['name']} ({api}): {error}")
                continue
            return stream, f"{info['name']} ({api})"
        raise RuntimeError("Could not open an audio output:\n" + "\n".join(errors))

    def post(self, kind, note=None):
        """Queue an event for the audio thread. kind is "on", "off" or "all_off"."""
        self.events.append((kind, note))

    def _callback(self, outdata, frames, time_info, status):
        start = time.perf_counter()
        if status.output_underflow:
            self.underruns += 1
        voice = self.voice
        events = self.events
        # Every event waiting at the start of the block is applied right here, so
        # a live note can start late by up to the time between callbacks. With
        # ASIO that is typically one block: 5.3 ms at 256 frames, 2.7 ms at 128,
        # and the block size is the trade between that delay and the risk of
        # missing the deadline. WASAPI's shared mode instead asks for several
        # blocks back to back, once per device period (about 10 ms on the laptop
        # this was measured on). There the period sets the delay, and smaller
        # blocks do not help.
        while events:
            kind, note = events.popleft()
            if kind == "on":
                voice.note_on(note)
            elif kind == "off":
                voice.note_off(note)
            else:
                voice.all_notes_off()
        if voice.active:
            left = outdata[:, 0]
            for i in range(frames):
                left[i] = voice.tick() * LIVE_GAIN
            outdata[:, 1:] = outdata[:, :1]  # the same mono signal on every channel
        else:
            # A silent voice would produce zeros anyway. Skipping the per-sample
            # loop just saves the work.
            outdata.fill(0.0)
        busy = time.perf_counter() - start
        self.blocks += 1
        self.busy_seconds += busy
        if busy > self.busy_peak:
            self.busy_peak = busy

    def status(self):
        budget = self.blocksize / SAMPLE_RATE
        average = self.busy_seconds / self.blocks if self.blocks else 0.0
        return (
            f"Output: {self.device_name}\n"
            f"Block: {self.blocksize} frames ({budget * 1000:.1f} ms). "
            f"Reported output latency: {self.latency * 1000:.1f} ms\n"
            f"Underruns: {self.underruns}. Callback uses {average / budget:.0%} of its "
            f"time on average, {self.busy_peak / budget:.0%} at peak"
        )

    def start(self):
        self.stream.start()

    def close(self):
        # Stopping the stream mid-note would cut the waveform off: one last click.
        # So release everything, wait for the release to reach silence, and only
        # then stop. stop() lets the blocks already queued play out.
        self.post("all_off")
        deadline = time.monotonic() + 5.0
        while (self.events or self.voice.active) and self.stream.active and time.monotonic() < deadline:
            time.sleep(0.01)
        self.stream.stop()
        self.stream.close()


def open_midi_inputs(engine):
    """Open every MIDI input port. mido delivers each message on its own thread."""
    import mido

    def on_message(msg):
        if msg.type == "note_on" and msg.velocity > 0:
            engine.post("on", msg.note)
        elif msg.type in ("note_on", "note_off"):
            # Many keyboards end a note with note-on at velocity 0 rather than a
            # real note-off. MIDI's running status lets a run of messages of the
            # same type skip their status byte, so staying on note-on saves a byte
            # per note on a slow serial link.
            engine.post("off", msg.note)
        elif msg.type == "control_change" and msg.control in (120, 123):  # all sound off, all notes off
            engine.post("all_off")

    ports = []
    for name in mido.get_input_names():
        try:
            ports.append(mido.open_input(name, callback=on_message))
        except Exception as error:  # rtmidi raises its own error types
            print(f"Could not open MIDI input {name!r}: {error}")
    return ports


def run_keyboard_window(engine):
    """Two rows of computer keys as a piano, for when no MIDI keyboard is connected.

    It is a window rather than the terminal because a terminal reports only key
    presses, and note-off needs releases. Windows repeats a held key as more
    presses with no release in between, so a press for a key already down is
    ignored.
    """
    import tkinter as tk

    root = tk.Tk()
    root.title("HARDPAN")
    label = tk.Label(root, font=("Consolas", 12), justify="left", anchor="w", padx=24, pady=20)
    label.pack()
    down = set()

    def press(event):
        key = event.keysym.lower()
        if key == "escape":
            root.destroy()
        elif key in KEY_TO_NOTE and key not in down:
            down.add(key)
            engine.post("on", KEY_TO_NOTE[key])

    def release(event):
        key = event.keysym.lower()
        if key in down:
            down.discard(key)
            engine.post("off", KEY_TO_NOTE[key])

    def release_all(event=None):
        # A key held while the window loses focus never sends its release, so let
        # go of everything rather than leave a note stuck on.
        for key in down:
            engine.post("off", KEY_TO_NOTE[key])
        down.clear()

    def refresh():
        held = list(engine.voice.held)  # copied, since the audio thread changes it
        playing = note_name(held[-1]) if held else "-"
        label.configure(text=f"{KEY_LAYOUT}\n\nPlaying: {playing}\n\n{engine.status()}")
        root.after(200, refresh)

    root.bind("<KeyPress>", press)
    root.bind("<KeyRelease>", release)
    root.bind("<FocusOut>", release_all)
    refresh()
    root.mainloop()


def run_live(blocksize=256):
    # sounddevice only offers ASIO if this is set before it is first imported.
    os.environ.setdefault("SD_ENABLE_ASIO", "1")
    import sounddevice as sd

    # The audio callback is Python, so it must take the interpreter lock before
    # it can start. A thread that is running gets to hold that lock for up to the
    # switch interval, 5 ms by default, which is nearly a whole 256-frame block.
    # 1 ms keeps that wait well inside the deadline.
    sys.setswitchinterval(0.001)

    engine = LiveEngine(sd, blocksize)
    ports = open_midi_inputs(engine)
    engine.start()
    print(engine.status())
    try:
        if ports:
            print("MIDI in:", ", ".join(port.name for port in ports))
            print("Ctrl+C to stop.")
            while True:
                time.sleep(0.2)
        else:
            print("No MIDI input found, so opening the computer-keyboard window.")
            run_keyboard_window(engine)
    except KeyboardInterrupt:
        pass
    finally:
        for port in ports:  # first, so no new note arrives during the final release
            port.close()
        engine.close()
        print(engine.status())


def play_arpeggio():
    import sounddevice as sd
    import soundfile as sf

    # C major arpeggio up to E5 and back: C4 E4 G4 C5 E5 C5 G4 E4, ending
    # on a held C4. Each note ends 50 ms before the next begins, so every new
    # attack starts partway through the previous note's release. That is the
    # boundary where a careless envelope clicks.
    pitches = [60, 64, 67, 72, 76, 72, 67, 64]
    step = 0.25
    notes = [(p, i * step, 0.20) for i, p in enumerate(pitches)]
    notes.append((60, len(pitches) * step, 0.80))

    audio = render(notes)

    # The raw peak is just the envelope's peak, 1.0, because there is no gain
    # staging yet. Scaling the peak to 0.8 leaves about 2 dB of headroom, so the
    # file cannot clip during playback or any later resampling.
    peak = float(np.max(np.abs(audio)))
    if peak > 0.0:
        audio *= F32(0.8 / peak)

    path = Path(__file__).with_name("hardpan_proto.wav")
    sf.write(path, audio, SAMPLE_RATE, subtype="FLOAT")  # 32-bit float: the file holds exactly what was rendered
    print(f"Wrote {path.name}: {len(audio) / SAMPLE_RATE:.2f} s, peak {np.max(np.abs(audio)):.3f}")

    sd.play(audio, SAMPLE_RATE)
    sd.wait()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "live":
        run_live(int(sys.argv[2]) if len(sys.argv) > 2 else 256)
    else:
        play_arpeggio()
