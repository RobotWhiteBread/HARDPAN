"""HARDPAN phase 1: a monophonic sine synth, rendered offline.

One sine oscillator and one ADSR envelope, driven by a list of
(note_number, start_time, duration) tuples and rendered sample by sample into
a 48 kHz mono float32 buffer. All of the synthesis math is written out in this
file. numpy holds the samples, soundfile writes the WAV, and sounddevice plays it.

Intentionally not implemented yet:
- Live MIDI input. Notes come from a list. mido comes in when rendering moves
  into a real-time audio callback.
- Real-time rendering. The whole buffer is rendered first and then played.
- Polyphony. There is one voice. A note that starts while another is held takes
  the voice over (last-note priority), and releasing it falls back to a note
  that is still held.
- Velocity, pitch bend, glide, sustain pedal.
- Any waveform other than sine. A sine has no harmonics above its fundamental,
  so it cannot alias, and anti-aliasing waits for saw and square (phase 2).
- Filters (phase 3).
- Smoothing for parameters changed while a note sounds. Parameters stay fixed
  for the length of a render.
- Denormal protection. The release snaps to exactly 0.0 instead of decaying
  forever, so nothing here lingers near zero yet (phase 3).
"""

import math
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


def render(notes, attack=0.010, decay=0.150, sustain=0.7, release=0.300):
    """Render (note_number, start_time, duration) tuples, with times in seconds,
    to a mono float32 buffer at 48 kHz. The buffer runs until the last release
    has reached silence."""
    osc = SineOscillator()
    env = ADSR(attack, decay, sustain, release)

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

    # One voice, last-note priority. The held list remembers which keys are
    # still down. Without it, playing legato (B starts before A ends) and then
    # ending A would silence B, because A's note-off would release the voice.
    held = []

    def note_on(note):
        if note in held:
            held.remove(note)
        held.append(note)
        if not env.active:
            # A silent voice has nothing to stay continuous with, so restart the
            # phase. Each note then begins at sin(0) = 0 and every attack sounds
            # alike. While the voice is sounding, the phase is never reset: that
            # would jump the waveform mid-cycle, which is a click.
            osc.reset()
        osc.set_frequency(midi_to_hz(note))
        env.note_on()

    def note_off(note):
        if note not in held:
            return
        was_sounding = held[-1] == note
        held.remove(note)
        if not was_sounding:
            return
        if held:
            osc.set_frequency(midi_to_hz(held[-1]))  # back to the key still held, no retrigger
        else:
            env.note_off()

    out = []
    n = 0
    i = 0
    while i < len(events) or env.active:
        while i < len(events) and events[i][0] == n:
            _, kind, note = events[i]
            note_on(note) if kind else note_off(note)
            i += 1
        # The envelope is a gain applied to the oscillator, sample by sample.
        # Multiplying is what amplitude means: an envelope of 0.5 is half the
        # waveform's height, which is 6 dB quieter.
        out.append(osc.tick() * env.tick())
        n += 1
    return np.array(out, dtype=F32)


if __name__ == "__main__":
    import sounddevice as sd
    import soundfile as sf

    # C major arpeggio up two octaves and back: C4 E4 G4 C5 E5 C5 G4 E4, ending
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
