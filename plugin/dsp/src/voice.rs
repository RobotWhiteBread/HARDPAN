//! Voices, the 8-voice allocator with stealing, and the gliding mono synth.
//! See `PolyVoice`, `PolySynth` and `MonoSynth` in `hardpan_proto.py`.
//!
//! Every field a voice needs lives inside it, with fixed sizes, so a synth is one
//! block of memory built before playback; nothing here allocates.

use crate::drive::Drive;
use crate::envelope::Adsr;
use crate::filter::{LowPassFilter, Slope};
use crate::oscillator::{Oscillator, Wave};

/// The filter's coefficients are recomputed every this many samples (a control rate).
pub const CONTROL_INTERVAL: u32 = 8;
pub const STEAL_FADE_SECONDS: f64 = 0.005;
pub const ACCENT_SECONDS: f64 = 0.003;

#[derive(Clone, Copy, Debug)]
pub struct OscSpec {
    pub wave: Wave,
    pub octave: i32,
    pub cents: f64,
    /// 0 turns the oscillator off.
    pub level: f32,
    pub width: f32,
}

impl OscSpec {
    pub const OFF: OscSpec = OscSpec { wave: Wave::Saw, octave: 0, cents: 0.0, level: 0.0, width: 0.5 };
}

/// Everything that defines a sound. Times in seconds; as in the prototype.
#[derive(Clone, Copy, Debug)]
pub struct Patch {
    pub oscillators: [OscSpec; 3],
    pub cutoff: f64,
    pub q: f64,
    pub slope: Slope,
    pub env_amount: f64,
    pub keytrack: f64,
    pub filter_env: [f64; 4],
    pub amp: [f64; 4],
    pub gain: f32,
    /// 0 bypasses the drive entirely.
    pub drive: f64,
    pub oversample: u8,
    pub glide: f64,
    pub accent_gain: f64,
    pub accent_env: f64,
}

#[derive(Clone, Copy, Debug)]
pub struct Voice {
    oscillators: [Oscillator; 3],
    levels: [f32; 3],
    ratios: [f64; 3],
    drive: Drive,
    drive_on: bool,
    pub amp_env: Adsr,
    pub filter_env: Adsr,
    filter: LowPassFilter,
    patch: Patch,
    sample_rate: f64,
    pub note: i32,
    pub held: bool,
    started_at: u64,
    released_at: u64,
    pub fade: i32,
    fade_len: i32,
    pending: i32,
    pending_accent: bool,
    pending_since: u64,
    pending_held_for: u64,
    release_in: u64,
    pitch: f64,
    bend: f64,
    glide_target: f64,
    glide_step: f64,
    glide_left: u32,
    accent: f64,
    accent_target: f64,
    accent_smoothing: f64,
    control: u32,
    key_cutoff: f64,
}

impl Voice {
    pub fn new(patch: &Patch, sample_rate: f64) -> Self {
        let [a, d, s, r] = patch.amp;
        let [fa, fd, fs, fr] = patch.filter_env;
        let mut voice = Self {
            oscillators: [Oscillator::new(Wave::Saw, 0.5); 3],
            levels: [0.0; 3],
            ratios: [1.0; 3],
            drive: Drive::new(patch.drive, patch.oversample),
            drive_on: patch.drive > 0.0,
            amp_env: Adsr::new(a, d, s, r, sample_rate),
            filter_env: Adsr::new(fa, fd, fs, fr, sample_rate),
            filter: LowPassFilter::new(patch.slope, sample_rate),
            patch: *patch,
            sample_rate,
            note: -1,
            held: false,
            started_at: 0,
            released_at: 0,
            fade: -1,
            fade_len: libm::round(STEAL_FADE_SECONDS * sample_rate) as i32,
            pending: -1,
            pending_accent: false,
            pending_since: 0,
            pending_held_for: 0,
            release_in: 0,
            pitch: 60.0,
            bend: 0.0,
            glide_target: 60.0,
            glide_step: 0.0,
            glide_left: 0,
            accent: 0.0,
            accent_target: 0.0,
            accent_smoothing: 1.0 - libm::exp(-1.0 / (ACCENT_SECONDS * sample_rate)),
            control: 0,
            key_cutoff: patch.cutoff,
        };
        voice.set_patch(patch);
        voice
    }

    /// Take new settings, including while a note sounds. The plugin calls this
    /// every control period with smoothed values, so continuous changes glide.
    pub fn set_patch(&mut self, patch: &Patch) {
        for i in 0..3 {
            let spec = patch.oscillators[i];
            self.oscillators[i].wave = spec.wave;
            self.oscillators[i].width = spec.width;
            self.levels[i] = spec.level;
            self.ratios[i] = libm::pow(2.0, spec.octave as f64 + spec.cents / 1200.0);
        }
        let [a, d, s, r] = patch.amp;
        self.amp_env.set(a, d, s, r);
        let [a, d, s, r] = patch.filter_env;
        self.filter_env.set(a, d, s, r);
        self.filter.set_slope(patch.slope);
        self.drive_on = patch.drive > 0.0;
        if self.drive_on {
            self.drive.set_gain(patch.drive);
        }
        self.patch = *patch;
        self.set_pitch(self.pitch); // octave, cents, cutoff or keytrack may have moved
    }

    pub fn free(&self) -> bool {
        self.fade < 0 && !self.amp_env.active()
    }

    /// Pitch bend in semitones, on top of the note and any glide. It moves the
    /// oscillators only: the filter's key tracking stays on the played note, so
    /// bending a note does not also change its brightness.
    pub fn set_bend(&mut self, semitones: f64) {
        if semitones != self.bend {
            self.bend = semitones;
            self.set_pitch(self.pitch);
        }
    }

    pub fn bend(&self) -> f64 {
        self.bend
    }

    fn set_pitch(&mut self, pitch: f64) {
        let hz = 440.0 * libm::pow(2.0, (pitch + self.bend - 69.0) / 12.0);
        for i in 0..3 {
            self.oscillators[i].set_frequency((hz * self.ratios[i]) as f32, self.sample_rate as f32);
        }
        self.key_cutoff = self.patch.cutoff * libm::pow(2.0, self.patch.keytrack * (pitch - 60.0) / 12.0);
    }

    pub fn start(&mut self, note: i32, now: u64, accent: bool) {
        if !self.amp_env.active() {
            self.filter.reset();
            self.drive.reset();
            self.accent = if accent { 1.0 } else { 0.0 };
        }
        self.accent_target = if accent { 1.0 } else { 0.0 };
        self.note = note;
        self.held = true;
        self.started_at = now;
        self.release_in = 0;
        self.glide_left = 0;
        self.pitch = note as f64;
        self.set_pitch(self.pitch);
        self.control = 0;
        self.amp_env.note_on();
        self.filter_env.note_on();
    }

    /// Legato: a straight line in semitones over `samples`, envelopes untouched.
    pub fn glide_to(&mut self, note: i32, samples: u32, accent: bool) {
        self.note = note;
        self.held = true;
        self.release_in = 0;
        self.accent_target = if accent { 1.0 } else { 0.0 };
        self.glide_target = note as f64;
        self.glide_left = samples.max(1);
        self.glide_step = (self.glide_target - self.pitch) / self.glide_left as f64;
    }

    pub fn release(&mut self, now: u64) {
        self.held = false;
        self.released_at = now;
        self.release_in = 0;
        self.amp_env.note_off();
        self.filter_env.note_off();
    }

    pub fn steal(&mut self, note: i32, now: u64, accent: bool) {
        self.fade = 0;
        self.pending = note;
        self.pending_accent = accent;
        self.pending_since = now;
        self.pending_held_for = 0;
    }

    #[inline]
    pub fn tick(&mut self, now: u64) -> f32 {
        if self.release_in != 0 {
            self.release_in -= 1;
            if self.release_in == 0 {
                self.release(now);
            }
        }
        if self.glide_left != 0 {
            self.glide_left -= 1;
            self.pitch = if self.glide_left == 0 { self.glide_target } else { self.pitch + self.glide_step };
            self.set_pitch(self.pitch);
        }
        if self.accent != self.accent_target {
            self.accent += (self.accent_target - self.accent) * self.accent_smoothing;
            if (self.accent_target - self.accent).abs() < 1e-4 {
                self.accent = self.accent_target;
            }
        }
        let mut mix = 0.0f32;
        for i in 0..3 {
            if self.levels[i] != 0.0 {
                mix = mix + self.oscillators[i].tick() * self.levels[i];
            }
        }
        if self.drive_on {
            mix = self.drive.tick(mix);
        }
        let filter_level = self.filter_env.tick() as f64;
        if self.control <= 1 {
            self.control = CONTROL_INTERVAL;
            let octaves = (self.patch.env_amount + self.patch.accent_env * self.accent) * filter_level;
            self.filter.set(self.key_cutoff * libm::pow(2.0, octaves), self.patch.q);
        } else {
            self.control -= 1;
        }
        let mut out = self.filter.tick(mix) * self.amp_env.tick();
        if self.accent != 0.0 {
            out = out * (1.0 + (self.patch.accent_gain - 1.0) * self.accent) as f32;
        }
        if self.fade >= 0 {
            let gain = 0.5 * (1.0 + libm::cos(core::f64::consts::PI * (self.fade + 1) as f64 / self.fade_len as f64));
            out = out * gain as f32;
            self.fade += 1;
            if self.fade == self.fade_len {
                self.restart(now + 1);
            }
        }
        out
    }

    fn restart(&mut self, now: u64) {
        self.fade = -1;
        self.amp_env.reset();
        self.filter_env.reset();
        let (note, held_for, accent) = (self.pending, self.pending_held_for, self.pending_accent);
        self.pending = -1;
        self.start(note, now, accent);
        if held_for != 0 {
            self.release_in = held_for;
        }
    }
}

/// N voices and the decisions about which voice plays which note.
#[derive(Clone, Copy, Debug)]
pub struct PolySynth<const N: usize> {
    pub voices: [Voice; N],
    gain: f32,
    now: u64,
}

impl<const N: usize> PolySynth<N> {
    pub fn new(patch: &Patch, sample_rate: f64) -> Self {
        Self { voices: [Voice::new(patch, sample_rate); N], gain: patch.gain, now: 0 }
    }

    pub fn set_patch(&mut self, patch: &Patch) {
        for voice in &mut self.voices {
            voice.set_patch(patch);
        }
        self.gain = patch.gain;
    }

    pub fn active(&self) -> bool {
        self.voices.iter().any(|v| !v.free())
    }

    pub fn set_bend(&mut self, semitones: f64) {
        for voice in &mut self.voices {
            voice.set_bend(semitones);
        }
    }

    pub fn note_on(&mut self, note: i32, accent: bool) {
        for voice in &mut self.voices {
            if voice.fade >= 0 && voice.pending == note {
                voice.pending_held_for = 0;
                return;
            }
            if voice.fade < 0 && voice.note == note && !voice.free() {
                voice.start(note, self.now, accent);
                return;
            }
        }
        if let Some(voice) = self.voices.iter_mut().find(|v| v.free()) {
            voice.start(note, self.now, accent);
            return;
        }
        self.steal(note, accent);
    }

    fn steal(&mut self, note: i32, accent: bool) {
        let now = self.now;
        if self.voices.iter().all(|v| v.fade >= 0) {
            // Every voice is already fading for another note: hand this one to the
            // voice that finishes its fade soonest. (max_by_key returns the last of
            // equals, Python's max the first; reversing keeps the prototype's choice.)
            if let Some(voice) = self.voices.iter_mut().rev().max_by_key(|v| v.fade) {
                voice.pending = note;
                voice.pending_accent = accent;
                voice.pending_since = now;
                voice.pending_held_for = 0;
            }
            return;
        }
        let releasing = self
            .voices
            .iter_mut()
            .filter(|v| v.fade < 0 && !v.held)
            .min_by_key(|v| v.released_at);
        let victim = match releasing {
            Some(v) => v,
            None => self.voices.iter_mut().filter(|v| v.fade < 0).min_by_key(|v| v.started_at).unwrap(),
        };
        victim.steal(note, now, accent);
    }

    pub fn note_off(&mut self, note: i32) {
        let now = self.now;
        for voice in &mut self.voices {
            if voice.fade >= 0 && voice.pending == note {
                voice.pending_held_for = (now - voice.pending_since).max(1);
            } else if voice.fade < 0 && voice.held && voice.note == note {
                voice.release(now);
            }
        }
    }

    /// Release every voice (for example on a mode switch or a host stop).
    pub fn release_all(&mut self) {
        let now = self.now;
        for voice in &mut self.voices {
            if voice.held {
                voice.release(now);
            }
            if voice.fade >= 0 && voice.pending_held_for == 0 {
                voice.pending_held_for = 1; // its note will start, then release at once
            }
        }
    }

    #[inline]
    pub fn tick(&mut self) -> f32 {
        let mut out = 0.0f32;
        let now = self.now;
        for voice in &mut self.voices {
            if !voice.free() {
                out = out + voice.tick(now);
            }
        }
        self.now += 1;
        out * self.gain
    }
}

const MAX_HELD: usize = 32;

/// One voice with last-note priority and legato glide, for bass lines and leads.
#[derive(Clone, Copy, Debug)]
pub struct MonoSynth {
    pub voice: Voice,
    glide: u32,
    held: [i32; MAX_HELD],
    held_len: usize,
    gain: f32,
    now: u64,
    sample_rate: f64,
}

impl MonoSynth {
    pub fn new(patch: &Patch, sample_rate: f64) -> Self {
        let mut synth = Self {
            voice: Voice::new(patch, sample_rate),
            glide: 1,
            held: [0; MAX_HELD],
            held_len: 0,
            gain: patch.gain,
            now: 0,
            sample_rate,
        };
        synth.set_patch(patch);
        synth
    }

    pub fn set_patch(&mut self, patch: &Patch) {
        self.voice.set_patch(patch);
        self.glide = (libm::round(patch.glide * self.sample_rate) as u32).max(1);
        self.gain = patch.gain;
    }

    pub fn active(&self) -> bool {
        !self.voice.free()
    }

    pub fn set_bend(&mut self, semitones: f64) {
        self.voice.set_bend(semitones);
    }

    fn remove_held(&mut self, note: i32) -> bool {
        if let Some(i) = self.held[..self.held_len].iter().position(|&n| n == note) {
            self.held.copy_within(i + 1..self.held_len, i);
            self.held_len -= 1;
            true
        } else {
            false
        }
    }

    pub fn note_on(&mut self, note: i32, accent: bool) {
        let legato = self.held_len > 0 && !self.voice.free();
        self.remove_held(note);
        if self.held_len == MAX_HELD {
            self.held.copy_within(1.., 0); // forget the oldest key rather than allocate
            self.held_len -= 1;
        }
        self.held[self.held_len] = note;
        self.held_len += 1;
        if legato {
            self.voice.glide_to(note, self.glide, accent);
        } else {
            self.voice.start(note, self.now, accent);
        }
    }

    pub fn note_off(&mut self, note: i32) {
        if self.held_len == 0 {
            return;
        }
        let was_sounding = self.held[self.held_len - 1] == note;
        if !self.remove_held(note) || !was_sounding {
            return;
        }
        if self.held_len > 0 {
            let back_to = self.held[self.held_len - 1];
            let accent = self.voice.accent_target > 0.0;
            self.voice.glide_to(back_to, self.glide, accent);
        } else {
            self.voice.release(self.now);
        }
    }

    pub fn release_all(&mut self) {
        self.held_len = 0;
        if self.voice.held {
            self.voice.release(self.now);
        }
    }

    #[inline]
    pub fn tick(&mut self) -> f32 {
        let out = if self.voice.free() { 0.0 } else { self.voice.tick(self.now) };
        self.now += 1;
        out * self.gain
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    pub fn test_patch() -> Patch {
        Patch {
            oscillators: [
                OscSpec { wave: Wave::Sine, octave: 0, cents: 0.0, level: 1.0, width: 0.5 },
                OscSpec::OFF,
                OscSpec::OFF,
            ],
            cutoff: 20000.0,
            q: core::f64::consts::FRAC_1_SQRT_2,
            slope: Slope::Db12,
            env_amount: 0.0,
            keytrack: 0.0,
            filter_env: [0.0, 0.1, 1.0, 0.1],
            amp: [0.01, 0.1, 0.7, 0.3],
            gain: 1.0,
            drive: 0.0,
            oversample: 2,
            glide: 0.1,
            accent_gain: 1.0,
            accent_env: 0.0,
        }
    }

    #[test]
    fn eight_notes_take_eight_voices_and_a_ninth_steals_the_oldest() {
        let mut s: PolySynth<8> = PolySynth::new(&test_patch(), 48000.0);
        for k in 0..8 {
            s.note_on(60 + k, false);
            for _ in 0..100 {
                s.tick();
            }
        }
        s.note_on(70, false);
        let stolen: Vec<_> = s.voices.iter().filter(|v| v.fade >= 0).collect();
        assert_eq!(stolen.len(), 1);
        assert_eq!(stolen[0].note, 60);
        for _ in 0..240 {
            s.tick();
        }
        assert!(s.voices.iter().any(|v| v.note == 70 && v.held && v.fade < 0));
    }

    #[test]
    fn the_longest_released_voice_is_stolen_first() {
        let mut s: PolySynth<8> = PolySynth::new(&test_patch(), 48000.0);
        for k in 0..8 {
            s.note_on(60 + k, false);
            for _ in 0..100 {
                s.tick();
            }
        }
        for n in [63, 61, 66] {
            s.note_off(n);
            for _ in 0..50 {
                s.tick();
            }
        }
        s.note_on(80, false);
        let stolen: Vec<_> = s.voices.iter().filter(|v| v.fade >= 0).collect();
        assert_eq!(stolen[0].note, 63);
    }

    #[test]
    fn mono_glide_is_a_straight_line_in_semitones() {
        let mut s = MonoSynth::new(&test_patch(), 48000.0);
        s.note_on(57, false);
        for _ in 0..1000 {
            s.tick();
        }
        s.note_on(69, false); // legato: 57 is still held
        let stage = s.voice.amp_env.stage;
        for _ in 0..2400 {
            s.tick();
        }
        assert!((s.voice.pitch - 63.0).abs() < 1e-9, "half way: {}", s.voice.pitch);
        for _ in 0..2400 {
            s.tick();
        }
        assert_eq!(s.voice.pitch, 69.0);
        assert_ne!(stage, crate::envelope::Stage::Attack, "legato must not retrigger");
    }

    /// Rising zero crossings per second of a held note, after its attack.
    fn frequency(s: &mut MonoSynth) -> f64 {
        for _ in 0..4800 {
            s.tick();
        }
        let (mut last, mut first_at, mut last_at, mut count) = (s.tick(), 0.0f64, 0.0f64, 0u32);
        for i in 1..48000 {
            let x = s.tick();
            if last < 0.0 && x >= 0.0 {
                let at = i as f64 - (x / (x - last)) as f64;
                if count == 0 {
                    first_at = at;
                }
                last_at = at;
                count += 1;
            }
            last = x;
        }
        48000.0 * (count - 1) as f64 / (last_at - first_at)
    }

    #[test]
    fn pitch_bend_shifts_the_oscillators_by_semitones() {
        let mut s = MonoSynth::new(&test_patch(), 48000.0);
        s.note_on(69, false);
        assert!((frequency(&mut s) - 440.0).abs() < 0.01);
        s.set_bend(2.0);
        let up = 440.0 * libm::pow(2.0, 2.0 / 12.0);
        assert!((frequency(&mut s) - up).abs() < 0.01);
        s.set_bend(-12.0);
        assert!((frequency(&mut s) - 220.0).abs() < 0.01);
        // A note started while the bend is held starts bent too.
        s.note_off(69);
        s.note_on(81, false);
        assert!((frequency(&mut s) - 440.0).abs() < 0.01);
        s.set_bend(0.0);
        assert!((frequency(&mut s) - 880.0).abs() < 0.01);
    }
}
