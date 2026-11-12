//! Chorus, tempo delay and reverb. See `DelayLine`, `JunoChorus`, `TempoDelay`
//! and `Reverb` in `hardpan_proto.py` for the reasoning.
//!
//! Each effect takes its memory from the caller as anything that derefs to a
//! slice of f32 (`B: AsRef<[f32]> + AsMut<[f32]>`): a `Vec<f32>` made before
//! playback in the plugin, a `&'static mut [f32; N]` on the pedal. Each has a
//! `required_len` so the caller knows how much to provide.

pub struct DelayLine<B> {
    buffer: B,
    mask: usize,
    pos: usize,
}

impl<B: AsRef<[f32]> + AsMut<[f32]>> DelayLine<B> {
    /// The buffer length for delays up to `longest` samples: a power of two, so
    /// wrapping round is a bit mask.
    pub fn required_len(longest: usize) -> usize {
        (longest + 2).next_power_of_two()
    }

    pub fn new(buffer: B) -> Self {
        let len = buffer.as_ref().len();
        assert!(len.is_power_of_two(), "delay buffer length must be a power of two");
        Self { buffer, mask: len - 1, pos: 0 }
    }

    pub fn clear(&mut self) {
        self.buffer.as_mut().fill(0.0);
    }

    #[inline]
    pub fn write(&mut self, x: f32) {
        self.buffer.as_mut()[self.pos] = x;
        self.pos = (self.pos + 1) & self.mask;
    }

    /// The sample from `delay` samples ago (at least 1). Read before writing.
    #[inline]
    pub fn read(&self, delay: usize) -> f32 {
        self.buffer.as_ref()[self.pos.wrapping_sub(delay) & self.mask]
    }

    /// The signal `delay` samples ago, linearly interpolated between samples.
    #[inline]
    pub fn read_between(&self, delay: f64) -> f32 {
        let whole = delay as usize;
        let fraction = (delay - whole as f64) as f32;
        let newer = self.read(whole);
        let older = self.read(whole + 1);
        newer + (older - newer) * fraction
    }
}

/// Juno-style chorus: two delay lines swept in opposite directions by one slow
/// triangle wave, mono in, stereo out (dry plus chorus on each side).
pub struct JunoChorus<B> {
    line: DelayLine<B>,
    centre: f64,
    depth: f64,
    phase: f64,
    step: f64,
    mix: f32,
    sample_rate: f64,
}

impl<B: AsRef<[f32]> + AsMut<[f32]>> JunoChorus<B> {
    /// Longest centre plus depth this chorus supports.
    pub const MAX_MS: f64 = 12.0;

    pub fn required_len(sample_rate: f64) -> usize {
        DelayLine::<B>::required_len((Self::MAX_MS * sample_rate / 1000.0) as usize + 2)
    }

    pub fn new(buffer: B, sample_rate: f64, rate: f64, centre_ms: f64, depth_ms: f64, mix: f32) -> Self {
        let mut chorus = Self {
            line: DelayLine::new(buffer),
            centre: 0.0,
            depth: 0.0,
            phase: 0.0,
            step: 0.0,
            mix,
            sample_rate,
        };
        chorus.set(rate, centre_ms, depth_ms, mix);
        chorus
    }

    pub fn set(&mut self, rate: f64, centre_ms: f64, depth_ms: f64, mix: f32) {
        let depth_ms = depth_ms.min(centre_ms - 0.1).max(0.0);
        let centre_ms = centre_ms.min(Self::MAX_MS - depth_ms);
        self.centre = centre_ms * self.sample_rate / 1000.0;
        self.depth = depth_ms * self.sample_rate / 1000.0;
        self.step = rate / self.sample_rate;
        self.mix = mix;
    }

    /// Silence the delay line without touching its memory allocation.
    pub fn clear(&mut self) {
        self.line.clear();
        self.phase = 0.0;
    }

    #[inline]
    pub fn tick(&mut self, x: f32) -> (f32, f32) {
        let sweep = 4.0 * (self.phase - 0.5).abs() - 1.0; // triangle, -1 to 1
        self.phase += self.step;
        if self.phase >= 1.0 {
            self.phase -= 1.0;
        }
        let a = self.line.read_between(self.centre + self.depth * sweep);
        let b = self.line.read_between(self.centre - self.depth * sweep);
        self.line.write(x);
        (x + a * self.mix, x + b * self.mix)
    }
}

/// Ping-pong delay locked to the tempo, with a darkening low-pass and a tanh
/// limiter in the feedback loop. Mono send in, stereo repeats (wet only) out.
pub struct TempoDelay<B> {
    left: DelayLine<B>,
    right: DelayLine<B>,
    length: f64,
    target: f64,
    max_length: f64,
    glide: f64,
    feedback: f64,
    tone: f32,
    dark_left: f32,
    dark_right: f32,
    sample_rate: f64,
}

impl<B: AsRef<[f32]> + AsMut<[f32]>> TempoDelay<B> {
    /// Longest delay supported, per line.
    pub const MAX_SECONDS: f64 = 2.0;

    pub fn required_len(sample_rate: f64) -> usize {
        DelayLine::<B>::required_len((Self::MAX_SECONDS * sample_rate) as usize + 2)
    }

    pub fn new(left: B, right: B, sample_rate: f64, seconds: f64, feedback: f64, tone_hz: f64) -> Self {
        let mut delay = Self {
            left: DelayLine::new(left),
            right: DelayLine::new(right),
            length: 0.0,
            target: 0.0,
            max_length: Self::MAX_SECONDS * sample_rate,
            // A tempo change slides the read point over about 50 ms instead of
            // jumping it: a jump is a click, a slide is a brief tape-like bend.
            glide: 1.0 - libm::exp(-1.0 / (0.05 * sample_rate)),
            feedback,
            tone: 0.0,
            dark_left: 0.0,
            dark_right: 0.0,
            sample_rate,
        };
        delay.set(seconds, feedback, tone_hz);
        delay.length = delay.target;
        delay
    }

    /// `seconds` is rounded to a whole number of samples, as in the prototype.
    pub fn set(&mut self, seconds: f64, feedback: f64, tone_hz: f64) {
        self.target = libm::round(seconds * self.sample_rate).clamp(1.0, self.max_length);
        self.feedback = feedback;
        self.tone = libm::exp(-2.0 * core::f64::consts::PI * tone_hz / self.sample_rate) as f32;
    }

    /// Silence both lines and the feedback filters, keeping the memory.
    pub fn clear(&mut self) {
        self.left.clear();
        self.right.clear();
        self.dark_left = 0.0;
        self.dark_right = 0.0;
        self.length = self.target;
    }

    #[inline]
    pub fn tick(&mut self, x: f32) -> (f32, f32) {
        if self.length != self.target {
            self.length += (self.target - self.length) * self.glide;
            if (self.target - self.length).abs() < 1e-3 {
                self.length = self.target;
            }
        }
        let out_l = self.left.read_between(self.length);
        let out_r = self.right.read_between(self.length);
        self.dark_left = out_l + (self.dark_left - out_l) * self.tone;
        self.dark_right = out_r + (self.dark_right - out_r) * self.tone;
        self.left.write(x + (self.feedback * libm::tanh(self.dark_right as f64)) as f32);
        self.right.write((self.feedback * libm::tanh(self.dark_left as f64)) as f32);
        (out_l, out_r)
    }
}

pub fn next_prime(mut n: usize) -> usize {
    fn is_prime(k: usize) -> bool {
        if k < 2 || k % 2 == 0 {
            return k == 2;
        }
        let mut d = 3;
        while d * d <= k {
            if k % d == 0 {
                return false;
            }
            d += 2;
        }
        true
    }
    while !is_prime(n) {
        n += 1;
    }
    n
}

pub const COMB_MS: [f64; 8] = [25.3, 26.9, 28.9, 30.7, 32.2, 33.8, 35.3, 36.7];
pub const ALLPASS_MS: [f64; 4] = [12.6, 10.0, 7.7, 5.1];
pub const STEREO_SPREAD: usize = 23;
const LINES: usize = 24; // per channel: 8 combs then 4 all-passes; two channels

/// Moorer-style reverb: eight damped combs in parallel, then four all-passes,
/// per channel. Mono send in, stereo reverb (wet only) out.
pub struct Reverb<B> {
    storage: B,
    lengths: [usize; LINES],
    offsets: [usize; LINES],
    positions: [usize; LINES],
    stored: [f32; 16],
    feedback: f32,
    damp: f32,
    keep: f32,
}

impl<B: AsRef<[f32]> + AsMut<[f32]>> Reverb<B> {
    /// Prime lengths (in samples) for this sample rate, so no two share a factor.
    pub fn lengths(sample_rate: f64) -> [usize; LINES] {
        let mut lengths = [0usize; LINES];
        for channel in 0..2 {
            let extra = channel * STEREO_SPREAD;
            for (k, ms) in COMB_MS.iter().chain(ALLPASS_MS.iter()).enumerate() {
                lengths[channel * 12 + k] = next_prime(libm::round(ms * sample_rate / 1000.0) as usize + extra);
            }
        }
        lengths
    }

    pub fn required_len(sample_rate: f64) -> usize {
        Self::lengths(sample_rate).iter().sum()
    }

    pub fn new(storage: B, sample_rate: f64, size: f64, damping: f64) -> Self {
        let lengths = Self::lengths(sample_rate);
        assert!(storage.as_ref().len() >= lengths.iter().sum::<usize>(), "reverb storage too small");
        let mut offsets = [0usize; LINES];
        let mut at = 0;
        for (offset, len) in offsets.iter_mut().zip(lengths.iter()) {
            *offset = at;
            at += len;
        }
        let mut reverb = Self {
            storage,
            lengths,
            offsets,
            positions: [0; LINES],
            stored: [0.0; 16],
            feedback: 0.0,
            damp: 0.0,
            keep: 0.0,
        };
        reverb.set(size, damping);
        reverb
    }

    pub fn set(&mut self, size: f64, damping: f64) {
        self.feedback = size as f32;
        self.damp = damping as f32;
        self.keep = (1.0 - damping) as f32;
    }

    /// Silence the tail, keeping the memory.
    pub fn clear(&mut self) {
        self.storage.as_mut().fill(0.0);
        self.stored = [0.0; 16];
        self.positions = [0; LINES];
    }

    #[inline]
    pub fn tick(&mut self, x: f32) -> (f32, f32) {
        const G: f32 = 0.5;
        let send = x * 0.05f32;
        let storage = self.storage.as_mut();
        let mut out = [0.0f32; 2];
        for (channel, result) in out.iter_mut().enumerate() {
            let mut total = 0.0f32;
            for k in 0..8 {
                let line = channel * 12 + k;
                let i = self.positions[line];
                let cell = self.offsets[line] + i;
                let y = storage[cell];
                let s = y * self.keep + self.stored[channel * 8 + k] * self.damp;
                self.stored[channel * 8 + k] = s;
                storage[cell] = send + s * self.feedback;
                self.positions[line] = if i + 1 < self.lengths[line] { i + 1 } else { 0 };
                total = total + y;
            }
            for k in 0..4 {
                let line = channel * 12 + 8 + k;
                let i = self.positions[line];
                let cell = self.offsets[line] + i;
                let delayed = storage[cell];
                let w = total + delayed * G;
                storage[cell] = w;
                total = delayed - w * G;
                self.positions[line] = if i + 1 < self.lengths[line] { i + 1 } else { 0 };
            }
            *result = total;
        }
        (out[0], out[1])
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn delay_line_reads_back_exactly() {
        let mut d = DelayLine::new(vec![0.0f32; DelayLine::<Vec<f32>>::required_len(100)]);
        for n in 0..200 {
            if n >= 11 {
                assert_eq!(d.read(10), (n - 10) as f32);
                assert_eq!(d.read_between(10.25), (n - 10) as f32 - 0.25);
            }
            d.write(n as f32);
        }
    }

    #[test]
    fn reverb_lengths_are_prime_and_coprime() {
        let lengths = Reverb::<Vec<f32>>::lengths(48000.0);
        assert_eq!(&lengths[..8], &[1217, 1291, 1399, 1481, 1549, 1627, 1697, 1777]);
        for (i, a) in lengths.iter().enumerate() {
            assert_eq!(next_prime(*a), *a);
            for b in &lengths[i + 1..] {
                assert!(a == b || gcd(*a, *b) == 1);
            }
        }
    }

    fn gcd(a: usize, b: usize) -> usize {
        if b == 0 { a } else { gcd(b, a % b) }
    }

    #[test]
    fn delay_feedback_above_one_stays_bounded() {
        let len = TempoDelay::<Vec<f32>>::required_len(48000.0);
        let mut d = TempoDelay::new(vec![0.0; len], vec![0.0; len], 48000.0, 0.375, 1.5, 2500.0);
        let mut peak = 0.0f32;
        for n in 0..(30 * 48000) {
            let x = if n < 24000 { ((n * 7919) % 1000) as f32 / 625.0 - 0.8 } else { 0.0 };
            let (l, r) = d.tick(x);
            if n > 25 * 48000 {
                peak = peak.max(l.abs()).max(r.abs());
            }
            assert!(l.is_finite() && r.is_finite());
        }
        assert!(peak < 1.6, "peak {peak}");
    }
}
