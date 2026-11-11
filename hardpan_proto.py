"""HARDPAN phases 1 to 3b: a synth built from first principles.

Oscillators, envelopes, an oversampled drive, a resonant filter, an 8-voice
allocator and a gliding mono voice, a step sequencer, and chorus, delay and
reverb, computed one sample at a time in float32 at 48 kHz. All of the
synthesis math is written out in this file. numpy holds the samples, soundfile
writes the WAV, sounddevice talks to the sound card, and mido reads MIDI.

    python hardpan_proto.py            render a C major arpeggio to
                                       hardpan_proto.wav and play it (phase 1)
    python hardpan_proto.py live       play the sine voice live from a MIDI
                                       keyboard, or from the computer keyboard
                                       if none is connected (phase 1b)
    python hardpan_proto.py live 128   the same, with 128-frame audio blocks
    python compare_aliasing.py         naive versus PolyBLEP saw and square, as
                                       spectrograms and measurements (phase 2)
    python hardpan_proto.py demo       render the bass and pad presets to
                                       hardpan_bass.wav and hardpan_pad.wav and
                                       play them (phase 3)
    python hardpan_proto.py track      render a 30-second track in stereo to
                                       hardpan_track.wav and play it (phase 3b)
    python compare_drive.py            the drive with and without oversampling,
                                       as spectrograms and measurements (phase 3b)

Intentionally not implemented yet:
- Polyphony and the presets in live mode. Live mode still plays the phase 1
  sine voice: eight filtered voices of per-sample Python cannot keep up in real
  time. That is what the Rust port in phase 4 is for.
- Self-oscillation. The cookbook biquad is linear and stays stable at any
  resonance it allows, so it rings and peaks but never sustains a tone on its
  own. That needs a nonlinear filter with feedback, like a Moog ladder.
- Velocity (MIDI velocity is read and ignored), pitch bend, sustain pedal. Glide
  and accent exist for rendered mono patches, not in live mode.
- Sample-accurate timing for live notes. A live note starts at the beginning of
  the next audio callback, so it can be late by up to the time between
  callbacks (see LiveEngine._callback). The offline render is sample accurate
  because it knows every note in advance.
- Choosing an audio device or MIDI port by hand. Live mode takes ASIO if a
  driver is installed, then WASAPI, then the Windows default, and opens every
  MIDI input present at startup.
- Smoothing for parameters changed while a note sounds. Parameters stay fixed
  for the length of a render.
"""

import math
import os
import sys
import time
from collections import deque
from dataclasses import dataclass
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


class PhaseOscillator:
    """The phase accumulator every oscillator here is built on. It produces one
    sample per tick, so the frequency can change on any sample. Each subclass
    turns the phase into a waveform in tick()."""

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

    def advance(self):
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


class SineOscillator(PhaseOscillator):
    def tick(self):
        # Output before advancing, so a note that starts at phase 0 really
        # starts at sin(0) = 0.
        out = np.sin(TWO_PI * self.phase)
        self.advance()
        return out


class NaiveSaw(PhaseOscillator):
    """A sawtooth read straight off the phase. Kept for comparison: it aliases.

    An ideal sawtooth has a harmonic at every multiple of its frequency, each
    weaker only in proportion (the nth is 1/n as strong), with no end. Samples
    at 48 kHz can only represent frequencies below half that, 24 kHz, the
    Nyquist frequency. Harmonics above it do not disappear. They fold back
    down, mirrored at 24 kHz: a harmonic at 24 kHz + x is heard at 24 kHz - x.
    Those folded harmonics are not multiples of the note, so they sound like
    faint inharmonic whistles, and when the pitch rises, they fall.
    """

    def tick(self):
        # Over one cycle the phase climbs from 0 to 1 and drops back, which is
        # already a sawtooth. Doubling and subtracting 1 just centres it on zero,
        # running from -1 to +1. The drop at the wrap is what aliases.
        out = 2.0 * self.phase - 1.0
        self.advance()
        return out


class NaiveSquare(PhaseOscillator):
    """A pulse wave read straight off the phase. Kept for comparison: it aliases.

    width is the fraction of each cycle spent high. 0.5 gives a square, which
    has only odd harmonics and sounds hollow. Narrower pulses sound thinner and
    more nasal, and slowly sweeping the width (pulse-width modulation) is a
    signature 80s string and pad sound. Any width other than 0.5 also adds a
    constant offset (DC) of 2 * width - 1, which is inaudible but uses headroom.
    """

    def __init__(self, sample_rate=SAMPLE_RATE, width=0.5):
        super().__init__(sample_rate)
        self.width = F32(width)

    def tick(self):
        out = F32(1.0) if self.phase < self.width else F32(-1.0)
        self.advance()
        return out


# What a BLEP is.
#
# Any waveform with jumps in it can be split into a smooth part plus a set of
# instant steps. A saw is a smooth ramp plus one downward step per cycle; a
# square is two steps per cycle and nothing else. The smooth part is harmless.
# The steps are the whole aliasing problem: an instant jump contains energy at
# every frequency, and everything above 24 kHz folds back into the audible
# range.
#
# A BLEP (band-limited step) is what a step looks like after an ideal low-pass
# filter at the Nyquist frequency has removed everything that cannot be
# sampled. Mathematically, it is the integral of a sinc function. It rises
# through the jump over a few samples, rings a little on either side, and
# contains nothing that can alias. Replace every naive step with a BLEP and
# the waveform stops aliasing.
#
# The replacement is done by adding a correction: the BLEP minus the naive
# step. The two agree everywhere except right around the jump, where the BLEP
# is smeared out and the naive step is not. So the correction is near zero
# away from a jump and large only beside it, which means it only has to be
# computed near jumps. Everywhere else the naive waveform is already right.
# That is why the method is cheap.
#
# Why only within one sample of the jump: the exact correction rings on for
# many samples, shrinking slowly. PolyBLEP approximates it with two short
# polynomial pieces, one on each side of the jump, covering exactly one sample
# each. They come from replacing the ideal sinc with a triangle two samples
# wide and integrating. That is a crude low-pass, which is why PolyBLEP reduces
# aliasing a great deal but does not remove it, and why it slightly softens
# the highest harmonics too. Wider corrections (tabulated BLEPs, minBLEPs) do
# better and cost more.
#
# The other half of the trick is timing. A jump happens at an exact instant,
# which almost never falls on a sample. The naive waveform can only jump on a
# sample, so it rounds every jump to the sample grid, and that rounding error,
# a little different every cycle, is the aliasing seen in time rather than in
# frequency. t / dt below measures where inside the sample interval the jump
# really happened, so the correction places the step where it belongs.


def poly_blep(t, dt):
    """Correction for an upward jump of 2 at phase 0, sampled at phase t, where
    dt is the phase step per sample. Every jump in these waveforms is 2 high
    (from -1 to +1 or back), so it is scaled for that. Only valid while dt < 0.5,
    so that the region after a jump does not run into the region before the
    same jump one cycle later: true for any pitch below 24 kHz."""
    if t < dt:
        # Just after the jump. x runs from 0 to 1 across the sample interval
        # after it.
        x = t / dt
        return x + x - x * x - 1.0
    if t > 1.0 - dt:
        # Just before the next jump. x runs from -1 to 0 across the interval
        # before it.
        x = (t - 1.0) / dt
        return x * x + x + x + 1.0
    return F32(0.0)


class PolyBlepSaw(PhaseOscillator):
    """A sawtooth with each drop replaced by a PolyBLEP. See the BLEP note above."""

    def tick(self):
        t = self.phase
        # The saw drops by 2 at the wrap: a downward jump, so the correction is
        # subtracted. Right at the jump, naive and correction cancel to 0, the
        # midpoint of the drop.
        out = 2.0 * t - 1.0 - poly_blep(t, self.increment)
        self.advance()
        return out


class PolyBlepSquare(PhaseOscillator):
    """A pulse wave with both of its jumps replaced by PolyBLEPs.

    width as in NaiveSquare. Each correction reaches one sample either side of
    its jump, so the two stay separate only while two steps per sample fit
    inside both the high and the low part of the cycle. For a square that holds
    below 12 kHz; very narrow pulses run into each other at lower pitches.
    """

    def __init__(self, sample_rate=SAMPLE_RATE, width=0.5):
        super().__init__(sample_rate)
        self.width = F32(width)

    def tick(self):
        t, dt, width = self.phase, self.increment, self.width
        out = F32(1.0) if t < width else F32(-1.0)
        # Two jumps per cycle: up by 2 at phase 0, down by 2 at phase `width`.
        # The downward one gets the same correction, measured from where that
        # jump sits in the cycle, and subtracted.
        out = out + poly_blep(t, dt)
        since_drop = t - width if t >= width else t - width + 1.0
        out = out - poly_blep(since_drop, dt)
        self.advance()
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

    def reset(self):
        """Drop straight to silence. Only for a voice that has already been faded
        out (see PolyVoice), where the jump cannot be heard."""
        self.level = F32(0.0)
        self.distance = F32(0.0)
        self.stage = IDLE

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


def note_events(notes):
    """(sample, kind, note, accent) events from (note_number, start_time, duration)
    tuples, in time order. kind is 1 for note-on, 0 for note-off. A tuple may
    carry a fourth item, True for an accented note."""
    # Sample-accurate means each note-on and note-off happens on its own sample,
    # the nearest one to its time, rather than at the start of some block.
    # Note-offs sort before note-ons on the same sample (0 before 1). That way a
    # note repeated back to back is released and then retriggered. In the other
    # order, the new note would be released on the very sample it started.
    events = []
    for item in notes:
        note, start, duration = item[:3]
        accent = bool(item[3]) if len(item) > 3 else False
        on = round(start * SAMPLE_RATE)
        off = max(round((start + duration) * SAMPLE_RATE), on + 1)  # a note always lasts at least a sample
        events.append((on, 1, note, accent))
        events.append((off, 0, note, False))
    events.sort()
    return events


def render(notes, attack=0.010, decay=0.150, sustain=0.7, release=0.300):
    """Render (note_number, start_time, duration) tuples, with times in seconds,
    to a mono float32 buffer at 48 kHz. The buffer runs until the last release
    has reached silence."""
    voice = MonoVoice(attack, decay, sustain, release)
    events = note_events(notes)

    out = []
    n = 0
    i = 0
    while i < len(events) or voice.active:
        while i < len(events) and events[i][0] == n:
            _, kind, note, _ = events[i]
            voice.note_on(note) if kind else voice.note_off(note)
            i += 1
        out.append(voice.tick())
        n += 1
    return np.array(out, dtype=F32)


# Filter and polyphony (phase 3) ---------------------------------------------

# Anything quieter than this is flushed to exactly zero inside the filter; see
# Biquad.tick. 1e-20 is about -400 dB, far below anything audible.
FLUSH_BELOW = 1e-20


class Biquad:
    """One second-order filter section with low-pass coefficients from Robert
    Bristow-Johnson's Audio EQ Cookbook, run in direct form I.

    Every biquad computes the same difference equation:

        y[n] = b0 x[n] + b1 x[n-1] + b2 x[n-2] - a1 y[n-1] - a2 y[n-2]

    The b terms mix the current and two previous inputs. For a low-pass they
    are in the ratio 1 : 2 : 1, which cancels completely at the Nyquist
    frequency, the highest frequency there is. The a terms feed back the two
    previous outputs, and they set the cutoff and the resonance: the feedback
    makes the filter ring at the cutoff, and the less damped that ringing, the
    taller and narrower the resonant peak. The cookbook gets the coefficients
    by taking the classic analog low-pass, 1 / (s^2 + s/Q + 1), and mapping it
    onto samples with the bilinear transform, adjusted so the cutoff lands
    exactly where it is asked to.

    Direct form I keeps the past inputs and outputs themselves as its state.
    That matters because the filter envelope moves the cutoff on every sample.
    The other common forms store running sums already weighted by the old
    coefficients, so a fast sweep leaves their state out of step with the new
    coefficients. Plain past samples stay valid whatever the coefficients are.
    """

    def __init__(self, sample_rate=SAMPLE_RATE):
        self.sample_rate = float(sample_rate)
        self.b0 = self.b1 = self.b2 = self.a1 = self.a2 = F32(0.0)
        self.reset()

    def reset(self):
        self.x1 = self.x2 = self.y1 = self.y2 = F32(0.0)

    def set_lowpass(self, cutoff, q):
        # w0 is the cutoff as an angle around the unit circle, where half a turn
        # (pi) is the Nyquist frequency.
        w0 = 2.0 * math.pi * cutoff / self.sample_rate
        cos_w0 = math.cos(w0)
        # alpha is the damping. A larger Q means a smaller alpha, less damping, and
        # a taller, narrower peak at the cutoff.
        alpha = math.sin(w0) / (2.0 * q)
        # Dividing everything by a0 normalizes the equation so y[n] itself has a
        # coefficient of 1. As with the envelope, the arithmetic is done in double
        # precision and rounded to float32 once.
        a0 = 1.0 + alpha
        b1 = (1.0 - cos_w0) / a0
        self.b0 = self.b2 = F32(0.5 * b1)
        self.b1 = F32(b1)
        self.a1 = F32(-2.0 * cos_w0 / a0)
        self.a2 = F32((1.0 - alpha) / a0)

    def tick(self, x):
        y = self.b0 * x + self.b1 * self.x1 + self.b2 * self.x2 - self.a1 * self.y1 - self.a2 * self.y2
        # Denormals. float32 normally stores a number as a 24-bit fraction times a
        # power of two, down to about 1.2e-38. Below that it gives up bits of the
        # fraction to reach smaller numbers still, down to 1.4e-45. Those are the
        # subnormal, or denormal, numbers, and on many x86 CPUs arithmetic on them
        # takes a slow path: a hundred or more cycles instead of a few. A filter
        # whose input goes quiet keeps feeding its output back, shrinking it every
        # sample, so it slides down into that range and lingers there. In a
        # real-time callback the CPU cost can then jump while nothing audible is
        # happening, and the block misses its deadline. Flushing anything below
        # FLUSH_BELOW to exactly zero costs nothing audible and keeps the
        # arithmetic on the fast path. In Python the interpreter's own overhead
        # hides the slowdown; it matters in the Rust port.
        if -FLUSH_BELOW < y < FLUSH_BELOW:
            y = F32(0.0)
        self.x2 = self.x1
        self.x1 = x
        self.y2 = self.y1
        self.y1 = y
        return y


# The two section Qs of a fourth-order Butterworth low-pass: 1 / (2 cos(pi/8))
# and 1 / (2 cos(3 pi/8)). Their product is 1 / sqrt(2), about 0.707.
BUTTERWORTH_Q = (0.5412, 1.3066)


class LowPassFilter:
    """A resonant low-pass with a 12 or 24 dB per octave slope.

    12 dB: a single biquad. Above the cutoff, every doubling of frequency comes
    out 12 dB quieter.

    24 dB: two biquads in series, so the slope doubles. That is the slope of the
    Moog and Juno filters, and it is what makes a closed filter sound dark
    rather than merely quieter: the saw's bright harmonics fall away fast.

    What cascading does to resonance: two identical sections in series square
    the whole response, so the peak doubles in decibels as well as the slope,
    and it narrows. A Q of 4 peaks at +12 dB in one section and +24 dB in two.
    Even at Q 0.707, where one section is 3 dB down at the cutoff, two are 6 dB
    down, so the corner sounds lower and rounder than asked. Instead, the two
    sections here use the fourth-order Butterworth Qs, which together are
    exactly 3 dB down at the cutoff and flat below it, and resonance goes into
    the second section only. A cookbook low-pass's gain at its own cutoff equals
    its Q, so scaling the second section by q / 0.707 makes the gain at the
    cutoff exactly q in both modes: the same q means the same peak height.

    The cutoff is kept between 20 Hz and 45% of the sample rate. At or above
    the Nyquist frequency a cutoff means nothing, and well below 20 Hz the
    float32 coefficients get close enough to 2 and 1 that rounding starts to
    move the filter noticeably. q runs from 0.5 to MAX_Q.
    """

    MIN_CUTOFF = 20.0
    MAX_Q = 20.0

    def __init__(self, slope=24, sample_rate=SAMPLE_RATE):
        if slope not in (12, 24):
            raise ValueError(f"slope must be 12 or 24, got {slope}")
        self.slope = slope
        self.max_cutoff = 0.45 * sample_rate
        self.sections = [Biquad(sample_rate) for _ in range(slope // 12)]
        self._cutoff = self._q = None

    def reset(self):
        for section in self.sections:
            section.reset()

    def set(self, cutoff, q):
        cutoff = min(max(cutoff, self.MIN_CUTOFF), self.max_cutoff)
        q = min(max(q, 0.5), self.MAX_Q)
        if cutoff == self._cutoff and q == self._q:
            return  # nothing has moved, so skip the trigonometry
        self._cutoff, self._q = cutoff, q
        if self.slope == 12:
            self.sections[0].set_lowpass(cutoff, q)
        else:
            self.sections[0].set_lowpass(cutoff, BUTTERWORTH_Q[0])
            self.sections[1].set_lowpass(cutoff, BUTTERWORTH_Q[1] * q / math.sqrt(0.5))

    def tick(self, x):
        for section in self.sections:
            x = section.tick(x)
        return x


@dataclass
class Osc:
    """One oscillator in a voice. octave and cents offset it from the note
    played; level is its share of the mix; width is the pulse width, for square
    waves only.

    Why a few cents of detune sounds thicker: two oscillators a frequency df
    apart drift in and out of step, so where they line up they reinforce and
    where they oppose they cancel. Their kth harmonics are k * df apart, so each
    harmonic swells and dips at its own rate, k times faster than the
    fundamental. The spectrum never sits still, and the ear hears that constant
    motion as two sources rather than one: an ensemble, a thicker sound.

    What sets the beating rate: df itself. Cents are a ratio, so df grows with
    pitch: df = f * (2**(cents/1200) - 1), close to f * cents / 1731. Two saws
    8 cents apart on A2 (110 Hz) beat 0.5 times a second at the fundamental and
    5 times a second at the 10th harmonic. The same detune beats twice as fast
    an octave up, which is why a detune that sounds lush in the middle of the
    keyboard sounds sour at the top and barely moves at the bottom.
    """

    wave: str = "saw"  # "saw", "square" or "sine"
    octave: int = 0
    cents: float = 0.0
    level: float = 0.5
    width: float = 0.5


@dataclass
class Patch:
    """Everything that defines a sound. Times are in seconds."""

    oscillators: list  # up to three Osc
    cutoff: float  # Hz, with the filter envelope at zero, for middle C (keytrack shifts it per note)
    q: float  # resonance: the filter's gain at its cutoff, 0.707 for no peak
    slope: int  # 12 or 24 dB per octave
    env_amount: float  # how many octaves the filter envelope opens the cutoff at its peak
    keytrack: float  # 0: cutoff ignores the note; 1: cutoff follows the note's pitch exactly
    filter_env: tuple  # attack, decay, sustain, release
    amp: tuple  # attack, decay, sustain, release
    gain: float = 1.0
    # Phase 3b
    drive: float = 0.0  # gain into the tanh saturator between oscillators and filter; 0 bypasses it
    oversample: int = 2  # 1, 2 or 4: the rate the saturator runs at, as a multiple of 48 kHz (see Drive)
    mono: bool = False  # one voice, last-note priority, overlapping notes glide (MonoSynth)
    glide: float = 0.0  # seconds a legato glide takes, whatever the interval
    accent_gain: float = 1.0  # loudness of an accented note, as a multiple
    accent_env: float = 0.0  # extra octaves of filter sweep on an accented note


# How long a stolen voice takes to fade out before its new note starts. 5 ms
# matches the envelope's shortest stage: about the fastest fade that does not
# itself click. The shape is a raised cosine, half a cycle of a cosine from 1
# down to 0. Unlike a straight line it starts and ends flat, with no corners,
# and corners in a gain curve are small clicks of their own.
STEAL_FADE_SECONDS = 0.005
_FADE_LENGTH = round(STEAL_FADE_SECONDS * SAMPLE_RATE)
STEAL_FADE = np.array(
    [0.5 * (1.0 + math.cos(math.pi * (i + 1) / _FADE_LENGTH)) for i in range(_FADE_LENGTH)], dtype=F32
)


# Drive (phase 3b) -----------------------------------------------------------


def bessel_i0(x):
    """The modified Bessel function I0, summed from its power series. The Kaiser
    window below is built from it."""
    total = term = 1.0
    k = 1
    while term > 1e-12 * total:
        term *= (x / (2.0 * k)) ** 2
        total += term
        k += 1
    return total


def halfband_taps(length, stopband_db=80.0):
    """A half-band low-pass FIR filter, designed by windowing the ideal one.

    A half-band filter's cutoff is exactly a quarter of its sample rate: half way
    to Nyquist. That is where oversampling by two needs it, because at the
    doubled rate the original audio sits in the lower half of the spectrum and
    everything to be removed sits in the upper half.

    The ideal low-pass with that cutoff has the impulse response 0.5 * sinc(n/2),
    which goes on forever. Chopping it off abruptly makes the frequency response
    ripple, so it is tapered with a Kaiser window. The window's one parameter,
    beta, trades how sharply the filter goes from passing to stopping against how
    deeply it stops; Kaiser's own formula gives beta for a wanted stopband depth.
    The length then sets how sharp the transition is.

    Half-band filters also come with a discount. sinc(n/2) is zero at every even
    n except the centre, so every other tap is exactly zero, and the centre tap
    is exactly 0.5. The resamplers below use both facts to skip half the work.
    """
    if length % 4 != 3:
        raise ValueError("these half-band filters have 4k + 3 taps, so the centre tap is odd")
    beta = 0.1102 * (stopband_db - 8.7)  # Kaiser's formula, for stopbands deeper than 50 dB
    centre = (length - 1) // 2
    taps = np.zeros(length)
    for i in range(length):
        n = i - centre
        if n == 0:
            ideal = 0.5
        elif n % 2 == 0:
            ideal = 0.0
        else:
            ideal = math.sin(math.pi * n / 2) / (math.pi * n)
        r = 2.0 * i / (length - 1) - 1.0  # -1 to 1 across the filter
        taps[i] = ideal * bessel_i0(beta * math.sqrt(1.0 - r * r)) / bessel_i0(beta)
    return taps.astype(F32)


# The inner stage converts between 48 and 96 kHz and has to be sharp: it must
# keep everything up to about 19 kHz and remove everything above about 29 kHz.
# The outer stage, between 96 and 192 kHz, has room to spare (keep up to 26 kHz,
# remove from 70 kHz), so a much shorter filter does.
HALFBAND_INNER = halfband_taps(51)
HALFBAND_OUTER = halfband_taps(23)


class HalfbandUpsampler:
    """Doubles the sample rate: one sample in, two out.

    Doubling the rate means putting a zero between every pair of samples. That
    keeps the original spectrum and adds a mirror image of it above the old
    Nyquist frequency, which the half-band filter removes. (The zeros also halve
    the level, so the filter is doubled.) Since the inserted samples are zero and
    half of the filter's taps are zero, each output only ever needs half the
    filter. One output of each pair is a short filter over the inputs. The other
    meets only the centre tap, 0.5, times the doubling: a plain delayed copy of
    an input.
    """

    def __init__(self, taps):
        centre = (len(taps) - 1) // 2
        self.taps = (2.0 * taps[0::2]).astype(F32)  # the taps that are not zero, doubled
        self.copy_at = (centre - 1) // 2
        self.size = len(self.taps)
        self.history = np.zeros(2 * self.size, dtype=F32)
        self.pos = 0

    def reset(self):
        self.history[:] = 0.0

    def push(self, x):
        # Each sample is stored twice, size apart, so the newest `size` samples are
        # always one contiguous slice, newest first, with no wrapping to handle.
        self.pos = (self.pos - 1) % self.size
        self.history[self.pos] = self.history[self.pos + self.size] = x
        recent = self.history[self.pos : self.pos + self.size]
        return np.dot(self.taps, recent), recent[self.copy_at]


class HalfbandDownsampler:
    """Halves the sample rate: two samples in, one out. It low-passes first, so
    nothing above the new Nyquist frequency is left to fold back down, then keeps
    every other sample. Only the samples that are kept get computed."""

    def __init__(self, taps):
        self.taps = taps  # symmetric, so it needs no reversing for convolution
        self.size = len(taps)
        self.history = np.zeros(2 * self.size, dtype=F32)
        self.pos = 0

    def reset(self):
        self.history[:] = 0.0

    def _store(self, x):
        self.pos = (self.pos - 1) % self.size
        self.history[self.pos] = self.history[self.pos + self.size] = x

    def push(self, a, b):
        self._store(a)
        self._store(b)
        return np.dot(self.taps, self.history[self.pos : self.pos + self.size])


class Drive:
    """Saturation: tanh(gain * x), run at 1, 2 or 4 times the sample rate.

    Why tanh: it is nearly a straight line for quiet signals and bends smoothly
    toward +1 and -1 for loud ones, much as an overdriven transistor or valve
    stage does. Because it is symmetric, the harmonics it adds are odd ones. The
    output is divided by tanh(gain), so a full-scale input still peaks at full
    scale and turning up the drive adds grit rather than mostly adding level.

    Why oversample: bending a waveform adds harmonics, at multiples of what is
    already there, and those land above the Nyquist frequency just as easily as
    a naive saw's do, where they fold back down as aliases. Running the bend at
    four times the sample rate puts those harmonics below a Nyquist of 96 kHz
    instead. The filter on the way back down then removes everything above the
    audio band before it can fold. compare_drive.py measures the difference.

    Why the patches here use 2x: on a sine, which brings no high harmonics of
    its own, 4x keeps clearly cleaner than 2x once the note is above about
    1.5 kHz. But on a saw, as in these patches, 2x already removes about 13 dB of
    the drive's aliasing below 10 kHz and 4x removes no more. What remains comes
    from the saw itself: the little aliasing PolyBLEP leaves just under 24 kHz
    gets mixed down by the bend, and oversampling the drive cannot undo aliasing
    already in its input. At 2.5 times less CPU, 2x is the right trade there.
    """

    def __init__(self, gain, oversample=4):
        if oversample not in (1, 2, 4):
            raise ValueError(f"oversample must be 1, 2 or 4, got {oversample}")
        self.gain = float(gain)
        self.norm = 1.0 / math.tanh(self.gain)
        self.oversample = oversample
        self.up_inner = HalfbandUpsampler(HALFBAND_INNER)
        self.down_inner = HalfbandDownsampler(HALFBAND_INNER)
        self.up_outer = HalfbandUpsampler(HALFBAND_OUTER)
        self.down_outer = HalfbandDownsampler(HALFBAND_OUTER)

    def reset(self):
        for stage in (self.up_inner, self.down_inner, self.up_outer, self.down_outer):
            stage.reset()

    def shape(self, x):
        # Computed in double precision and rounded once, like the coefficients.
        return F32(math.tanh(self.gain * float(x)) * self.norm)

    def tick(self, x):
        if self.oversample == 1:
            return self.shape(x)
        a, b = self.up_inner.push(x)
        if self.oversample == 2:
            return self.down_inner.push(self.shape(a), self.shape(b))
        a1, a2 = self.up_outer.push(a)
        b1, b2 = self.up_outer.push(b)
        return self.down_inner.push(
            self.down_outer.push(self.shape(a1), self.shape(a2)),
            self.down_outer.push(self.shape(b1), self.shape(b2)),
        )


# Voices ---------------------------------------------------------------------

# The filter's coefficients are recomputed every CONTROL_INTERVAL samples rather
# than every sample. New coefficients cost a sine, a cosine and several divisions
# per section, which was most of a voice's time when done every sample. The
# envelopes still run every sample; only their effect on the filter is sampled,
# at 6 kHz. Cutoff steps that small and that frequent are far below what the ear
# can pick out as zipper noise. Hardware synths have long run their modulation
# at a control rate like this for the same reason.
CONTROL_INTERVAL = 8

# Accent changes a voice's loudness and filter sweep, possibly while it is already
# sounding (a legato note, or a retrigger during a release), so it is glided in
# rather than switched: a jump in gain is a click. 3 ms is short enough to land
# as part of the note's attack.
ACCENT_SMOOTHING = 1.0 - math.exp(-1.0 / (0.003 * SAMPLE_RATE))


class PolyVoice:
    """Up to three oscillators, mixed, through an optional drive and a low-pass
    filter, then an amplitude envelope: the classic oscillator, filter, amplifier
    chain. A second envelope moves the filter cutoff."""

    def __init__(self, patch, sample_rate=SAMPLE_RATE):
        if not 1 <= len(patch.oscillators) <= 3:
            raise ValueError("a voice mixes one to three oscillators")
        self.patch = patch
        self.oscillators = []
        for spec in patch.oscillators:
            if spec.wave == "saw":
                osc = PolyBlepSaw(sample_rate)
            elif spec.wave == "square":
                osc = PolyBlepSquare(sample_rate, spec.width)
            elif spec.wave == "sine":
                osc = SineOscillator(sample_rate)
            else:
                raise ValueError(f"unknown wave {spec.wave!r}")
            # Octaves double the frequency. Cents are hundredths of a semitone,
            # 1200 to the octave, so +4 cents multiplies it by 2**(4/1200), 1.0023.
            ratio = 2.0 ** (spec.octave + spec.cents / 1200.0)
            self.oscillators.append((osc, F32(spec.level), ratio))
        self.drive = Drive(patch.drive, patch.oversample) if patch.drive > 0 else None
        self.amp_env = ADSR(*patch.amp, sample_rate=sample_rate)
        self.filter_env = ADSR(*patch.filter_env, sample_rate=sample_rate)
        self.filter = LowPassFilter(patch.slope, sample_rate)
        self.note = None
        self.held = False
        self.started_at = 0
        self.released_at = 0
        self.fade = -1  # position in STEAL_FADE while being stolen, otherwise -1
        self.pending = None  # the note to start once the fade is done
        self.pending_accent = False
        self.pending_since = 0  # when that note was played
        self.pending_held_for = 0  # if its key was let go during the fade, for how long it was held
        self.release_in = 0  # samples until a delayed release, 0 for none
        self.pitch = 60.0  # in semitones (MIDI note numbers), fractional while gliding
        self.glide_target = 60.0
        self.glide_step = 0.0
        self.glide_left = 0
        self.accent = 0.0  # 0 to 1, glided toward accent_target
        self.accent_target = 0.0
        self.control = 0  # samples until the filter is next updated
        self.key_cutoff = patch.cutoff

    @property
    def free(self):
        return self.fade < 0 and not self.amp_env.active

    def _set_pitch(self, pitch):
        hz = 440.0 * 2.0 ** ((pitch - 69.0) / 12.0)
        for osc, _, ratio in self.oscillators:
            osc.set_frequency(hz * ratio)
        # Key tracking: the cutoff follows the note by `keytrack` octaves per octave,
        # measured from middle C, so a high note is not muffled relative to its own
        # harmonics while a low one is wide open.
        self.key_cutoff = self.patch.cutoff * 2.0 ** (self.patch.keytrack * (pitch - 60.0) / 12.0)

    def start(self, note, now, accent=False):
        """Start a note: on a free voice, on a voice already playing this note
        (a retrigger), or on a stolen voice once its fade has finished."""
        if not self.amp_env.active:
            # A silent voice: clear what is left of its last note, and take the new
            # accent straight away, since nothing can be heard to jump.
            self.filter.reset()
            if self.drive:
                self.drive.reset()
            self.accent = 1.0 if accent else 0.0
        self.accent_target = 1.0 if accent else 0.0
        self.note = note
        self.held = True
        self.started_at = now
        self.release_in = 0
        self.glide_left = 0
        # The oscillator phases are left running rather than reset to 0. Resetting
        # several oscillators together would start every note with all of them in
        # step, the same spike each time. Running on, as analog oscillators do,
        # each note starts from a slightly different alignment.
        self.pitch = float(note)
        self._set_pitch(self.pitch)
        self.control = 0  # update the filter on this very sample
        self.amp_env.note_on()
        self.filter_env.note_on()

    def glide_to(self, note, samples, accent=False):
        """Legato: slide to `note` over `samples` without restarting the envelopes.

        The slide is a straight line in semitones, so every semitone takes the
        same time, and the glide time is the same whatever the interval. Pitch is
        heard in ratios, so a straight line in semitones is an exponential curve
        in Hz. A straight line in Hz would sound lopsided: going up from A2 to A3,
        the first semitone is 6.5 Hz wide and the last 12.3 Hz, so a constant
        speed in Hz rushes through the low semitones and drags through the high
        ones, and the same glide time would sound different in every register.
        """
        self.note = note
        self.held = True
        self.release_in = 0
        self.accent_target = 1.0 if accent else 0.0
        self.glide_target = float(note)
        self.glide_left = max(1, samples)
        self.glide_step = (self.glide_target - self.pitch) / self.glide_left

    def release(self, now):
        self.held = False
        self.released_at = now
        self.release_in = 0
        self.amp_env.note_off()
        self.filter_env.note_off()

    def steal(self, note, now, accent=False):
        """Fade this voice out, then start `note` on it."""
        self.fade = 0
        self.pending = note
        self.pending_accent = accent
        self.pending_since = now
        self.pending_held_for = 0

    def tick(self, now):
        if self.release_in:
            self.release_in -= 1
            if not self.release_in:
                self.release(now)
        if self.glide_left:
            self.glide_left -= 1
            self.pitch = self.glide_target if not self.glide_left else self.pitch + self.glide_step
            self._set_pitch(self.pitch)
        if self.accent != self.accent_target:
            self.accent += (self.accent_target - self.accent) * ACCENT_SMOOTHING
            if abs(self.accent_target - self.accent) < 1e-4:
                self.accent = self.accent_target
        mix = F32(0.0)
        for osc, level, _ in self.oscillators:
            mix = mix + osc.tick() * level
        if self.drive:
            mix = self.drive.tick(mix)
        filter_level = float(self.filter_env.tick())
        self.control -= 1
        if self.control <= 0:
            self.control = CONTROL_INTERVAL
            # The envelope adds octaves to the cutoff, which multiplies it by 2 to
            # the power of that many octaves. Brightness, like pitch, is heard in
            # ratios: a sweep from 200 to 400 Hz sounds as big as 2 to 4 kHz.
            octaves = (self.patch.env_amount + self.patch.accent_env * self.accent) * filter_level
            self.filter.set(self.key_cutoff * 2.0**octaves, self.patch.q)
        out = self.filter.tick(mix) * self.amp_env.tick()
        if self.accent:
            out = out * F32(1.0 + (self.patch.accent_gain - 1.0) * self.accent)
        if self.fade >= 0:
            out = out * STEAL_FADE[self.fade]
            self.fade += 1
            if self.fade == len(STEAL_FADE):
                self._restart(now + 1)
        return out

    def _restart(self, now):
        # The fade has reached exactly zero, so the voice is silent and can be
        # cleared to a clean start without a click.
        self.fade = -1
        self.amp_env.reset()
        self.filter_env.reset()
        note, held_for = self.pending, self.pending_held_for
        self.pending = None
        self.start(note, now, self.pending_accent)
        if held_for:
            # The key was already let go during the fade. The note starts 5 ms
            # late, so its release comes 5 ms late too and it keeps its full
            # length. Releasing it now would cut a short note out entirely.
            self.release_in = held_for


class PolySynth:
    """Eight voices and the decisions about which voice plays which note."""

    VOICES = 8

    def __init__(self, patch, sample_rate=SAMPLE_RATE):
        self.voices = [PolyVoice(patch, sample_rate) for _ in range(self.VOICES)]
        self.gain = F32(patch.gain)
        self.now = 0  # samples since the start, for telling old notes from new

    @property
    def active(self):
        return any(not voice.free for voice in self.voices)

    def note_on(self, note, accent=False):
        for voice in self.voices:
            if voice.fade >= 0 and voice.pending == note:
                voice.pending_held_for = 0  # already on its way in, and held again
                return
            if voice.fade < 0 and voice.note == note and not voice.free:
                # The same note is still sounding: retrigger that voice from its
                # current level rather than stack a second copy, which would beat
                # and partly cancel against the first.
                voice.start(note, self.now, accent)
                return
        for voice in self.voices:
            if voice.free:
                voice.start(note, self.now, accent)
                return
        self._steal(note, accent)

    def _steal(self, note, accent=False):
        candidates = [voice for voice in self.voices if voice.fade < 0]
        if not candidates:
            # Every voice is already fading out for some other note. Hand this note
            # to the voice that will finish its fade soonest instead.
            voice = max(self.voices, key=lambda v: v.fade)
            voice.pending, voice.pending_accent = note, accent
            voice.pending_since, voice.pending_held_for = self.now, 0
            return
        releasing = [voice for voice in candidates if not voice.held]
        if releasing:
            # The voice released longest ago is the quietest and the closest to
            # finishing on its own, so cutting it short is the least noticeable.
            victim = min(releasing, key=lambda v: v.released_at)
        else:
            # Every voice is held. Take the oldest note: the one the ear has had
            # longest to get used to.
            victim = min(candidates, key=lambda v: v.started_at)
        victim.steal(note, self.now, accent)

    def note_off(self, note):
        for voice in self.voices:
            if voice.fade >= 0 and voice.pending == note:
                voice.pending_held_for = max(1, self.now - voice.pending_since)
            elif voice.fade < 0 and voice.held and voice.note == note:
                voice.release(self.now)

    def tick(self):
        out = F32(0.0)
        for voice in self.voices:
            if not voice.free:
                out = out + voice.tick(self.now)
        self.now += 1
        return out * self.gain


class MonoSynth:
    """One voice with last-note priority, for bass lines and leads.

    A note that starts while another key is still held is played legato: it
    glides from the old pitch without restarting the envelopes, the way a
    monophonic analog synth slides between tied notes. A detached note starts
    fresh. Letting go of the newest key glides back to one still held.
    """

    def __init__(self, patch, sample_rate=SAMPLE_RATE):
        self.voice = PolyVoice(patch, sample_rate)
        self.glide = max(1, round(patch.glide * sample_rate))
        self.held = []  # keys down, most recent last
        self.gain = F32(patch.gain)
        self.now = 0

    @property
    def active(self):
        return not self.voice.free

    def note_on(self, note, accent=False):
        legato = bool(self.held) and not self.voice.free
        if note in self.held:
            self.held.remove(note)
        self.held.append(note)
        if legato:
            self.voice.glide_to(note, self.glide, accent)
        else:
            self.voice.start(note, self.now, accent)

    def note_off(self, note):
        if note not in self.held:
            return
        was_sounding = self.held[-1] == note
        self.held.remove(note)
        if not was_sounding:
            return
        if self.held:
            self.voice.glide_to(self.held[-1], self.glide, self.voice.accent_target > 0)
        else:
            self.voice.release(self.now)

    def tick(self):
        out = self.voice.tick(self.now) if not self.voice.free else F32(0.0)
        self.now += 1
        return out * self.gain


def render_patch(notes, patch):
    """Render (note_number, start_time, duration[, accent]) tuples through a patch
    to a mono float32 buffer at 48 kHz, running until every voice is silent: the
    single gliding voice for a mono patch, the eight voices otherwise."""
    synth = MonoSynth(patch) if patch.mono else PolySynth(patch)
    events = note_events(notes)
    out = []
    n = 0
    i = 0
    while i < len(events) or synth.active:
        while i < len(events) and events[i][0] == n:
            _, kind, note, accent = events[i]
            synth.note_on(note, accent) if kind else synth.note_off(note)
            i += 1
        out.append(synth.tick())
        n += 1
    return np.array(out, dtype=F32)


# Presets ------------------------------------------------------------------
# Both aim at the README's sound target: dark, driving, 80s. They are starting
# points for ears, not answers; every value is there to be moved.

BASS = Patch(
    oscillators=[
        # Two saws 4 cents either side of the note. A saw has every harmonic, which
        # is what a filter needs to have something to sweep through. The small
        # detune makes each harmonic of one beat slowly against the same harmonic
        # of the other, so the tone moves. It is kept small because the fundamental
        # of a bass note carries its weight, and wide detune makes that fundamental
        # swell and dip.
        Osc("saw", cents=-4.0, level=0.5),
        Osc("saw", cents=+4.0, level=0.5),
        # A square an octave down, for weight that does not beat, since nothing
        # else sits at that pitch. A square has only odd harmonics, so it adds the
        # low octave without thickening the saws' own harmonics.
        Osc("square", octave=-1, level=0.45),
    ],
    slope=24,  # steep, so the closed filter sounds dark rather than just quieter
    # Mostly closed: at rest only the lowest few harmonics get through. With key
    # tracking, the A2 the line sits on rests at about 145 Hz.
    cutoff=220.0,
    q=3.5,  # a moderate peak, +11 dB at the cutoff: the sweep reads as a vowel-like "yow", not a whistle
    env_amount=4.0,  # every note throws the cutoff up 4 octaves, to about 2.3 kHz on A2: the snap
    keytrack=0.5,  # higher notes open the filter a little more, so the line stays even
    # Filter envelope: as fast an attack as the envelope allows (5 ms), then a
    # 600 ms decay. An envelope time here is the time to finish, and the curve is
    # exponential, so most of the movement comes early: two-thirds of the way
    # closed after about 65 ms. In a 16th-note line that puts the bite in each
    # note's first 50 to 100 ms. Sustain 0: while a note is held the filter goes
    # all the way back to the resting cutoff.
    filter_env=(0.0, 0.6, 0.0, 0.08),
    # Amplitude: instant attack for punch, a slight decay so the front of each
    # note stands out, and a short release so the 16ths stay separate instead of
    # smearing into a drone.
    amp=(0.0, 0.3, 0.75, 0.06),
    gain=0.5,
)

PAD = Patch(
    oscillators=[
        # Wider detune than the bass, 8 cents either side. Over long, slow chords
        # the beating is the pad's movement, and the pad's low notes are doubled
        # by other chord tones, so a wavering fundamental matters less.
        Osc("saw", cents=-8.0, level=0.45),
        Osc("saw", cents=+8.0, level=0.45),
        # A quiet square an octave down for warmth underneath.
        Osc("square", octave=-1, level=0.2),
    ],
    slope=24,
    # The pad never snaps open the way the bass does, so it never sounds bright.
    # But it keeps the 2nd to 4th harmonics of its chord tones, which is where a
    # pad's body is. Much lower, and the chords collapse into murky sine tones.
    cutoff=550.0,
    q=0.9,  # nearly no peak: resonance on stacked chord tones whistles
    env_amount=1.5,  # a slow swell of an octave and a half as each chord blooms, to about 1.5 kHz
    keytrack=0.4,
    # The filter opens over 1.5 s, settles over 2.5 s at 40% of its sweep (about
    # 830 Hz), and closes over 3 s after release, slightly slower than the sound
    # fades.
    filter_env=(1.5, 2.5, 0.4, 3.0),
    # Slow attack so chord changes swell in rather than strike; long release so
    # one chord dissolves into the next. That overlap is also what pushes the
    # chord changes past 8 voices.
    amp=(0.9, 1.0, 0.85, 2.5),
    gain=0.3,
)


def bass_line(bpm=110, bars=8):
    """A 16th-note line in A minor: the root pumping, with an octave jump every
    third note (a 3+3+3+3+4 pattern that pulls against the beat), over A, F, D
    and E, two bars each. E major rather than E minor: the raised G sharp of the
    harmonic minor gives the turnaround its menace."""
    step = 60.0 / bpm / 4
    roots = [45, 45, 41, 41, 38, 38, 40, 40]  # A2, F2, D2, E2
    pattern = [0, 0, 12, 0, 0, 12, 0, 0, 12, 0, 0, 12, 0, 12, 7, 12]
    notes = []
    for bar in range(bars):
        for i, interval in enumerate(pattern):
            start = (bar * 16 + i) * step
            notes.append((roots[bar % len(roots)] + interval, start, 0.55 * step))
    return notes


def pad_progression(bpm=110):
    """The same progression as slow chords, two bars each, then an A minor 9
    chord rolled in one note at a time. Each chord change overlaps the last
    chord's release, so it needs 10 voices and steals the releasing ones. The
    final chord needs 10 held voices, so as its 9th and 10th notes arrive its
    two lowest notes are faded out to make room: the stealing you can hear."""
    bar = 4 * 60.0 / bpm
    chords = [
        [45, 52, 57, 60, 64],  # A minor: A2 E3 A3 C4 E4
        [41, 48, 57, 60, 65],  # F major: F2 C3 A3 C4 F4
        [38, 50, 57, 62, 65],  # D minor: D2 D3 A3 D4 F4
        [40, 52, 56, 59, 64],  # E major: E2 E3 G#3 B3 E4
    ]
    notes = []
    for i, chord in enumerate(chords):
        notes += [(note, i * 2 * bar, 2 * bar) for note in chord]
    rolled_at = len(chords) * 2 * bar
    final = [33, 45, 52, 57, 60, 64, 67, 69, 71, 72]  # A1 A2 E3 A3 C4 E4 G4 A4 B4 C5
    end = rolled_at + 0.3 * len(final) + 4.0
    notes += [(note, rolled_at + 0.3 * k, end - (rolled_at + 0.3 * k)) for k, note in enumerate(final)]
    return notes


# Sequencer (phase 3b) -------------------------------------------------------


@dataclass
class Step:
    """One step of a 16th-note sequence.

    note: a MIDI note, or None for a rest.
    gate: how much of the step the note is held. Below 1 the notes are detached.
      Above 1 a note runs into the next step, which on a mono patch makes the
      next note glide from it, as a tie did on 80s sequencers.
    accent: play this step louder and with more filter sweep.
    """

    note: object = None
    gate: float = 0.5
    accent: bool = False


def sequence(steps, bpm, start=0.0, swing=0.0):
    """Turn 16th-note steps into (note, start, duration, accent) tuples.

    swing pushes every second 16th late by that fraction of a step. 0 is
    straight, and about 0.33 puts the off-beat 16ths on a triplet: a full
    shuffle. Small amounts, 0.03 to 0.1, do not sound swung so much as less
    mechanical.
    """
    step = 60.0 / bpm / 4
    notes = []
    for i, s in enumerate(steps):
        if s.note is None:
            continue
        at = start + i * step + (swing * step if i % 2 else 0.0)
        notes.append((s.note, at, s.gate * step, s.accent))
    return notes


# Effects (phase 3b) ---------------------------------------------------------


class DelayLine:
    """A circular buffer that remembers the most recent stretch of a signal, so
    any point in that stretch can be read back."""

    def __init__(self, longest):
        size = 1
        while size < longest + 2:
            size *= 2  # a power of two, so wrapping round is a bit mask
        self.buffer = np.zeros(size, dtype=F32)
        self.mask = size - 1
        self.pos = 0

    def write(self, x):
        self.buffer[self.pos] = x
        self.pos = (self.pos + 1) & self.mask

    def read(self, delay):
        """The sample from `delay` samples ago, at least 1. Read before writing."""
        return self.buffer[(self.pos - delay) & self.mask]

    def read_between(self, delay):
        """The signal `delay` samples ago, where delay need not be a whole number.

        Why interpolate: a chorus sweeps its delay smoothly, so the delay it
        wants is almost never a whole number of samples. Rounding it would make
        the read position jump a whole sample each time the rounded value ticks
        over. Every jump is a small step in the output, and dozens a second add
        up to a zipper-like crackle. The pitch shift, which follows how fast the
        delay changes, would also come out as a jittery staircase instead of a
        smooth sweep. Linear interpolation draws a straight line between the two
        neighbouring samples and reads off the point in between, so the output
        moves as smoothly as the delay does. A straight line is only a rough
        guess at the signal between samples: it dulls the top end slightly, by
        an amount that varies with the fraction. Cubic or all-pass interpolation
        guess better. For a chorus, whose 80s originals ran through dark
        bucket-brigade chips anyway, linear is enough.
        """
        whole = int(delay)
        fraction = F32(delay - whole)
        newer = self.buffer[(self.pos - whole) & self.mask]
        older = self.buffer[(self.pos - whole - 1) & self.mask]
        return newer + (older - newer) * fraction


class JunoChorus:
    """A chorus in the style of the Roland Juno: two short delay lines whose
    times are swept by one slow triangle wave, in opposite directions, one per
    stereo side.

    A delay whose length is changing plays its input back at a slightly
    different speed, which shifts its pitch. As the delay shortens the output
    catches up and the pitch rises; as it lengthens the pitch falls. Mixed with
    the dry signal, that gently detuned copy sounds like a second player: the
    same thickening as detuned oscillators, applied after the fact. With the
    defaults the delay moves 3.6 ms every second, a speed change of 0.36%,
    about 6 cents either way.

    The two sides sweep in opposite directions, so while the left copy is sharp
    the right one is flat. That spreads the sound wide in stereo, and summed to
    mono the two detunings average out, so it stays in tune. A triangle wave
    sweeps at a constant speed, so the detune holds steady and flips at each
    turn, which is part of the Juno's sound; a sine would be smoother and vaguer.

    The defaults, about half a cycle per second sweeping between roughly 1.7 and
    5.3 ms, are close to the figures usually quoted for the Juno-60's Chorus I.
    """

    def __init__(self, rate=0.5, centre_ms=3.5, depth_ms=1.8, mix=0.7, sample_rate=SAMPLE_RATE):
        self.centre = centre_ms * sample_rate / 1000.0
        self.depth = depth_ms * sample_rate / 1000.0
        self.line = DelayLine(int(self.centre + self.depth) + 2)
        self.phase = 0.0
        self.step = rate / sample_rate
        self.mix = F32(mix)

    def process(self, signal):
        """A mono signal in, a stereo pair (dry plus chorus on each side) out."""
        left = np.empty(len(signal), dtype=F32)
        right = np.empty(len(signal), dtype=F32)
        line, centre, depth, mix = self.line, self.centre, self.depth, self.mix
        for n, x in enumerate(signal):
            sweep = 4.0 * abs(self.phase - 0.5) - 1.0  # triangle wave, -1 to 1
            self.phase += self.step
            if self.phase >= 1.0:
                self.phase -= 1.0
            a = line.read_between(centre + depth * sweep)
            b = line.read_between(centre - depth * sweep)
            line.write(x)
            left[n] = x + a * mix
            right[n] = x + b * mix
        return left, right


class TempoDelay:
    """A ping-pong delay locked to the tempo: repeats alternate left and right,
    each one darker than the last.

    The delay time is a number of beats. The default, 0.75, is a dotted 8th, the
    classic 80s choice: its repeats land off the grid of the 16th-note sequence,
    filling the gaps rather than doubling the notes.

    Feedback sends each repeat back in for another pass. Below 1, every pass is
    quieter and the repeats die away. At 1 or more, every pass would be as loud
    or louder, and the repeats would build without end. Two things sit in the
    loop to stop that and to shape it. A one-pole low-pass makes every pass lose
    a little more top end, as tape and bucket-brigade delays did. A tanh limiter
    barely touches quiet repeats but squashes loud ones, and it can never return
    more than `feedback` in amplitude, so the loop's gain shrinks as its level
    grows. Turned past 1, the delay swells into a self-sustaining wash at a
    bounded level instead of blowing up.
    """

    def __init__(self, bpm, beats=0.75, feedback=0.45, tone=2500.0, sample_rate=SAMPLE_RATE):
        self.length = round(beats * 60.0 / bpm * sample_rate)
        self.left = DelayLine(self.length)
        self.right = DelayLine(self.length)
        self.feedback = float(feedback)
        # The one-pole low-pass keeps this share of its previous output each
        # sample; exp(-2 pi fc / fs) puts its -3 dB point near fc.
        self.tone = F32(math.exp(-2.0 * math.pi * tone / sample_rate))
        self.dark_left = self.dark_right = F32(0.0)

    def process(self, signal):
        """A mono send in, the stereo repeats (wet only) out."""
        left = np.empty(len(signal), dtype=F32)
        right = np.empty(len(signal), dtype=F32)
        for n, x in enumerate(signal):
            out_l = self.left.read(self.length)
            out_r = self.right.read(self.length)
            self.dark_left = out_l + (self.dark_left - out_l) * self.tone
            self.dark_right = out_r + (self.dark_right - out_r) * self.tone
            # Ping-pong: the left line takes the input plus the right line's
            # repeats, the right line only the left's. A sound goes in, comes out
            # on the left, then the right, then the left again.
            self.left.write(x + F32(self.feedback * math.tanh(float(self.dark_right))))
            self.right.write(F32(self.feedback * math.tanh(float(self.dark_left))))
            left[n] = out_l
            right[n] = out_r
        return left, right


def next_prime(n):
    def is_prime(k):
        if k < 2 or k % 2 == 0:
            return k == 2
        return all(k % d for d in range(3, int(k**0.5) + 1, 2))

    while not is_prime(n):
        n += 1
    return n


class Reverb:
    """A Moorer-style reverb: eight comb filters in parallel, each with damping,
    then four all-pass filters in series. Built twice, with slightly different
    lengths, for the left and right channels.

    A comb filter is a delay line fed back into itself. A sound goes round and
    round, coming out once per trip, a little quieter each time. One comb is a
    regular, buzzy echo; eight in parallel with different lengths blur into a
    wash. The all-passes then smear each echo out in time without changing the
    overall tone, since they pass every frequency at the same level and only
    shift its timing. That thickens the wash into a smooth tail.

    Damping: a one-pole low-pass inside each comb's loop, so high frequencies
    lose more on every trip than low ones. The tail darkens as it fades, as it
    does in a real room, where air and soft surfaces soak up the highs first.

    Why the comb lengths share no common factors: a comb's echoes repeat every
    D samples, and it resonates at every multiple of 48000 / D Hz. Combs whose
    lengths share a factor keep echoing at the same moments and resonate at the
    same frequencies, which piles up into a fluttering repeat and a metallic
    ring. Lengths with no common factor (here each one is a prime number) only
    line up after the product of their lengths, minutes later, so the echoes
    interleave densely and the resonances spread evenly through the spectrum.
    """

    COMB_MS = (25.3, 26.9, 28.9, 30.7, 32.2, 33.8, 35.3, 36.7)
    ALLPASS_MS = (12.6, 10.0, 7.7, 5.1)
    STEREO_SPREAD = 23  # samples added to every right-channel length
    ALLPASS_GAIN = 0.5

    def __init__(self, size=0.88, damping=0.5, sample_rate=SAMPLE_RATE):
        # size is each comb's feedback. With the combs about 31 ms long, 0.88
        # gives a tail of around 1.7 s before damping shortens its top end.
        self.size = size
        self.damping = damping
        self.lengths = []
        for extra in (0, self.STEREO_SPREAD):
            combs = [next_prime(round(ms * sample_rate / 1000.0) + extra) for ms in self.COMB_MS]
            allpasses = [next_prime(round(ms * sample_rate / 1000.0) + extra) for ms in self.ALLPASS_MS]
            self.lengths.append((combs, allpasses))

    def process(self, signal):
        """A mono send in, the stereo reverb (wet only) out."""
        feedback, damp = F32(self.size), F32(self.damping)
        keep, g = F32(1.0 - self.damping), F32(self.ALLPASS_GAIN)
        # Eight combs feeding back at 0.88 add up to a large gain, so the input is
        # scaled down to keep the tail near the level of what went in.
        send = (signal * F32(0.05)).astype(F32)
        channels = []
        for comb_lengths, allpass_lengths in self.lengths:
            combs = [np.zeros(n, dtype=F32) for n in comb_lengths]
            comb_at = [0] * len(combs)
            stored = [F32(0.0)] * len(combs)
            allpasses = [np.zeros(n, dtype=F32) for n in allpass_lengths]
            allpass_at = [0] * len(allpasses)
            out = np.empty(len(send), dtype=F32)
            for n, x in enumerate(send):
                total = F32(0.0)
                for k, buffer in enumerate(combs):
                    i = comb_at[k]
                    y = buffer[i]
                    s = y * keep + stored[k] * damp  # the damping low-pass
                    stored[k] = s
                    buffer[i] = x + s * feedback
                    comb_at[k] = i + 1 if i + 1 < comb_lengths[k] else 0
                    total = total + y
                for k, buffer in enumerate(allpasses):
                    # The all-pass: w = x + g * w[n-D], y = w[n-D] - g * w. Its
                    # gain is 1 at every frequency; only the timing is smeared.
                    i = allpass_at[k]
                    delayed = buffer[i]
                    w = total + delayed * g
                    buffer[i] = w
                    total = delayed - w * g
                    allpass_at[k] = i + 1 if i + 1 < allpass_lengths[k] else 0
                out[n] = total
            channels.append(out)
        return channels[0], channels[1]


# The 3b track ---------------------------------------------------------------

SEQ_BASS = Patch(
    oscillators=[
        Osc("saw", cents=-4.0, level=0.5),
        Osc("saw", cents=+4.0, level=0.5),
        # The square an octave down is quieter than in the phase 3 bass. On these
        # roots it sits at 37 to 55 Hz, and at 0.45 it put nearly half the track's
        # energy below 80 Hz: headroom spent on weight most speakers cannot play.
        Osc("square", octave=-1, level=0.3),
    ],
    # Driven hard into the filter. The saturation adds odd harmonics for the
    # resonant filter to chew on, which is most of the "hard" in hard-driving.
    drive=2.5,
    slope=24,
    cutoff=200.0,  # a little lower than the phase 3 bass, since the drive brightens it
    q=3.5,
    env_amount=3.5,
    keytrack=0.5,
    filter_env=(0.0, 0.5, 0.0, 0.08),
    amp=(0.0, 0.3, 0.75, 0.06),
    mono=True,
    glide=0.06,  # tied steps slide over 60 ms, under half a 16th at 110 BPM
    accent_gain=1.6,  # accented steps are 4 dB louder...
    accent_env=1.2,  # ...and their filter opens 1.2 octaves further: they bark
    gain=0.5,
)

LEAD = Patch(
    oscillators=[
        Osc("saw", cents=-6.0, level=0.5),
        # A slightly narrow pulse against the saw: hollow and a little nasal, a
        # classic 80s lead blend.
        Osc("square", width=0.4, cents=+6.0, level=0.35),
    ],
    drive=1.6,  # light drive: warmth and sustain, not fuzz
    slope=24,
    # Open enough that the lead's upper harmonics reach 2 to 5 kHz, where the ear
    # is most sensitive, so it cuts through the dark mix without sounding bright.
    cutoff=1400.0,
    q=2.0,
    env_amount=1.5,
    keytrack=0.6,  # the lead spans an octave and a half, so the filter follows it more closely
    # The filter opens over 40 ms rather than snapping, so the lead sings
    # rather than plucks, then settles a little under halfway.
    filter_env=(0.04, 0.9, 0.45, 0.6),
    amp=(0.012, 0.6, 0.85, 0.35),
    mono=True,
    glide=0.11,  # slower than the bass: audible, vocal slides between tied notes
    gain=0.5,
)

TRACK_BPM = 110
# Two bars per root: A minor, i i VI iv V i. E major at the V, as in phase 3.
TRACK_ROOTS = [45, 45, 41, 38, 40, 45]  # A2 A2 F2 D2 E2 A2
# Pad chords: (notes, first bar, number of bars), counting bars from 0.
PAD_CHORDS = [
    ([57, 60, 64, 69], 0, 4),  # A minor: A3 C4 E4 A4
    ([57, 60, 65, 69], 4, 2),  # F major: A3 C4 F4 A4
    ([57, 62, 65, 69], 6, 2),  # D minor: A3 D4 F4 A4
    ([56, 59, 64, 68], 8, 2),  # E major: G#3 B3 E4 G#4
    ([57, 60, 64, 69], 10, 3),  # A minor, held into the ending
]
# One bar of bass: (interval above the root, gate, accent). Accents fall on
# every third 16th, a dotted-8th pulse that pulls against the 4/4 beat, with
# the octave jumps between them. The last three steps are tied, so they glide:
# up the octave, down to the fifth, back up.
BASS_BAR = [
    (0, 0.5, True), (0, 0.5, False), (12, 0.5, False),
    (0, 0.5, True), (0, 0.5, False), (12, 0.5, False),
    (0, 0.5, True), (0, 0.5, False), (12, 0.5, False),
    (0, 0.5, True), (0, 0.5, False), (12, 0.5, False),
    (0, 0.5, True), (12, 1.1, False), (7, 1.1, False), (12, 0.5, False),
]  # fmt: skip
# The lead enters at bar 5: (note, start beat, length in beats). A note that
# runs past the next one's start is tied, and glides into it.
LEAD_LINE = [
    (76, 16.0, 1.5), (74, 17.5, 0.5), (72, 18.0, 2.1), (77, 20.0, 1.6), (76, 21.5, 2.0),
    (74, 24.0, 2.1), (77, 26.0, 1.1), (81, 27.0, 2.5),
    (77, 29.5, 0.45), (76, 30.0, 0.45), (74, 30.5, 0.45), (72, 31.0, 0.9),
    (71, 32.0, 2.1), (68, 34.0, 1.6), (71, 35.5, 0.6), (74, 36.0, 2.1), (76, 38.0, 2.0),
    (72, 40.0, 2.1), (71, 42.0, 1.1), (69, 43.0, 4.5),
]  # fmt: skip


def track_parts(bpm=TRACK_BPM):
    """(bass, pad, lead) note lists for the 3b demo: 12 bars plus an ending."""
    bar, beat = 4 * 60.0 / bpm, 60.0 / bpm
    steps = []
    for b in range(12):
        root = TRACK_ROOTS[b // 2]
        steps += [Step(root + interval, gate, accent) for interval, gate, accent in BASS_BAR]
    steps.append(Step(TRACK_ROOTS[-1], gate=6.0, accent=True))  # the last note
    bass = sequence(steps, bpm, swing=0.04)
    pad = [(note, first * bar, bars * bar) for chord, first, bars in PAD_CHORDS for note in chord]
    lead = [(note, start * beat, length * beat) for note, start, length in LEAD_LINE]
    return bass, pad, lead


def at_loudness(signal, dbfs):
    """`signal` scaled so its RMS level, measured over the stretch where it is
    actually playing, comes out at `dbfs`. Parts are balanced by loudness
    rather than by peak level, since a spiky bass and a smooth pad with the same
    peak sound nothing alike in level."""
    peak = float(np.max(np.abs(signal)))
    playing = np.flatnonzero(np.abs(signal) > 1e-4 * peak) if peak > 0 else []
    if not len(playing):
        return signal
    span = signal[playing[0] : playing[-1] + 1].astype(np.float64)
    rms = math.sqrt(float(np.mean(span * span)))
    return (signal * F32(10.0 ** (dbfs / 20.0) / rms)).astype(F32)


def render_track(bpm=TRACK_BPM):
    """The 3b demo: sequenced bass, pad and gliding lead, through chorus, delay
    and reverb. Returns a stereo float32 array, peak 0.8."""
    bass_notes, pad_notes, lead_notes = track_parts(bpm)
    parts = {}
    for name, notes, patch in (("bass", bass_notes, SEQ_BASS), ("pad", pad_notes, PAD), ("lead", lead_notes, LEAD)):
        started = time.perf_counter()
        print(f"  {name}...", end="", flush=True)
        parts[name] = render_patch(notes, patch)
        print(f" {time.perf_counter() - started:.0f} s", flush=True)
    length = max(len(part) for part in parts.values()) + round(3.5 * SAMPLE_RATE)  # room for the tails
    bass, pad, lead = (np.pad(parts[k], (0, length - len(parts[k]))) for k in ("bass", "pad", "lead"))
    bass = at_loudness(bass, -15.0)
    pad = at_loudness(pad, -22.0)
    lead = at_loudness(lead, -18.0)

    started = time.perf_counter()
    print("  effects...", end="", flush=True)
    pad_l, pad_r = JunoChorus(rate=0.5).process(pad)
    # A faster, shallower chorus on the lead, nearer the Juno's Chorus II, so the
    # two parts do not swirl in step.
    lead_l, lead_r = JunoChorus(rate=0.83, depth_ms=1.2, mix=0.5).process(lead)
    echo_l, echo_r = TempoDelay(bpm).process((lead * F32(0.7) + pad * F32(0.15)).astype(F32))
    send = (pad * F32(0.6) + lead * F32(0.45) + bass * F32(0.12) + (echo_l + echo_r) * F32(0.3)).astype(F32)
    verb_l, verb_r = Reverb().process(send)
    verb = np.concatenate([verb_l, verb_r]).astype(np.float64)
    verb_scale = F32(10.0 ** (-22.0 / 20.0) / max(math.sqrt(float(np.mean(verb * verb))), 1e-12))
    print(f" {time.perf_counter() - started:.0f} s", flush=True)

    left = bass + pad_l * F32(0.7) + lead_l * F32(0.75) + echo_l * F32(0.5) + verb_l * verb_scale
    right = bass + pad_r * F32(0.7) + lead_r * F32(0.75) + echo_r * F32(0.5) + verb_r * verb_scale
    stereo = np.stack([left, right], axis=1).astype(F32)
    return stereo * F32(0.8 / float(np.max(np.abs(stereo))))


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


def play_demo():
    """Render the phase 3 presets to WAV files, then play them: the bass line,
    then the pad progression."""
    import sounddevice as sd
    import soundfile as sf

    renders = []
    for name, notes, patch in (("bass", bass_line(), BASS), ("pad", pad_progression(), PAD)):
        print(f"Rendering the {name}...", flush=True)
        started = time.perf_counter()
        audio = render_patch(notes, patch)
        peak = float(np.max(np.abs(audio)))
        if peak > 0.0:
            audio *= F32(0.8 / peak)  # same 0.8 peak as the arpeggio, for the same reason
        path = Path(__file__).with_name(f"hardpan_{name}.wav")
        sf.write(path, audio, SAMPLE_RATE, subtype="FLOAT")
        print(f"  wrote {path.name}: {len(audio) / SAMPLE_RATE:.1f} s, rendered in {time.perf_counter() - started:.0f} s")
        renders.append(audio)
    for audio in renders:
        sd.play(audio, SAMPLE_RATE)
        sd.wait()


def play_track():
    """Render the phase 3b demo to hardpan_track.wav (stereo) and play it."""
    import sounddevice as sd
    import soundfile as sf

    print("Rendering the track, which takes a few minutes...", flush=True)
    started = time.perf_counter()
    stereo = render_track()
    path = Path(__file__).with_name("hardpan_track.wav")
    sf.write(path, stereo, SAMPLE_RATE, subtype="FLOAT")
    print(f"Wrote {path.name}: {len(stereo) / SAMPLE_RATE:.1f} s, rendered in {time.perf_counter() - started:.0f} s")
    sd.play(stereo, SAMPLE_RATE)
    sd.wait()


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "live":
        run_live(int(sys.argv[2]) if len(sys.argv) > 2 else 256)
    elif command == "demo":
        play_demo()
    elif command == "track":
        play_track()
    else:
        play_arpeggio()
