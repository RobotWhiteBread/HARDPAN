//! tanh saturation, oversampled with hand-designed half-band filters.
//! See `halfband_taps`, `HalfbandUpsampler`, `HalfbandDownsampler` and `Drive`
//! in `hardpan_proto.py`.

use core::f64::consts::PI;

fn bessel_i0(x: f64) -> f64 {
    let mut total = 1.0;
    let mut term = 1.0;
    let mut k = 1.0;
    while term > 1e-12 * total {
        let half = x / (2.0 * k);
        term *= half * half;
        total += term;
        k += 1.0;
    }
    total
}

/// A Kaiser-windowed half-band low-pass with `L` taps (L = 4k + 3).
pub fn halfband_taps<const L: usize>(stopband_db: f64) -> [f32; L] {
    assert!(L % 4 == 3, "half-band filters here have 4k + 3 taps");
    let beta = 0.1102 * (stopband_db - 8.7);
    let centre = (L - 1) / 2;
    let mut taps = [0.0f32; L];
    for (i, tap) in taps.iter_mut().enumerate() {
        let n = i as i64 - centre as i64;
        let ideal = if n == 0 {
            0.5
        } else if n % 2 == 0 {
            0.0
        } else {
            libm::sin(PI * n as f64 / 2.0) / (PI * n as f64)
        };
        let r = 2.0 * i as f64 / (L - 1) as f64 - 1.0;
        *tap = (ideal * bessel_i0(beta * libm::sqrt(1.0 - r * r)) / bessel_i0(beta)) as f32;
    }
    taps
}

/// Dot product of `taps` with a ring buffer read newest first from `pos`.
#[inline]
fn ring_dot<const N: usize>(taps: &[f32; N], ring: &[f32; N], pos: usize) -> f32 {
    // Writes move backwards through the ring, so newest first means ring[pos..]
    // followed by ring[..pos], which wrapped round.
    let mut acc = 0.0f32;
    let (wrapped, from_newest) = ring.split_at(pos);
    for (t, x) in taps.iter().zip(from_newest.iter().chain(wrapped.iter())) {
        acc += t * x;
    }
    acc
}

/// Doubles the sample rate. `P` is the number of non-zero taps per phase.
#[derive(Clone, Copy, Debug)]
pub struct HalfbandUpsampler<const P: usize> {
    taps: [f32; P],
    copy_at: usize,
    ring: [f32; P],
    pos: usize,
}

impl<const P: usize> HalfbandUpsampler<P> {
    /// From a full half-band design with 2P - 1 taps.
    pub fn new(full: &[f32]) -> Self {
        assert_eq!(full.len(), 2 * P - 1);
        let centre = (full.len() - 1) / 2;
        let mut taps = [0.0f32; P];
        for (i, tap) in taps.iter_mut().enumerate() {
            *tap = 2.0 * full[2 * i]; // the taps that are not zero, doubled for the inserted zeros
        }
        Self { taps, copy_at: (centre - 1) / 2, ring: [0.0; P], pos: 0 }
    }

    pub fn reset(&mut self) {
        self.ring = [0.0; P];
    }

    /// One sample in, two out, in time order.
    #[inline]
    pub fn push(&mut self, x: f32) -> (f32, f32) {
        self.pos = (self.pos + P - 1) % P;
        self.ring[self.pos] = x;
        let filtered = ring_dot(&self.taps, &self.ring, self.pos);
        let copy = self.ring[(self.pos + self.copy_at) % P];
        (filtered, copy)
    }
}

/// Halves the sample rate. `L` is the full number of taps.
#[derive(Clone, Copy, Debug)]
pub struct HalfbandDownsampler<const L: usize> {
    taps: [f32; L],
    ring: [f32; L],
    pos: usize,
}

impl<const L: usize> HalfbandDownsampler<L> {
    pub fn new(taps: [f32; L]) -> Self {
        Self { taps, ring: [0.0; L], pos: 0 }
    }

    pub fn reset(&mut self) {
        self.ring = [0.0; L];
    }

    #[inline]
    fn store(&mut self, x: f32) {
        self.pos = (self.pos + L - 1) % L;
        self.ring[self.pos] = x;
    }

    /// Two samples in, one out.
    #[inline]
    pub fn push(&mut self, a: f32, b: f32) -> f32 {
        self.store(a);
        self.store(b);
        ring_dot(&self.taps, &self.ring, self.pos)
    }
}

pub const INNER_TAPS: usize = 51;
pub const OUTER_TAPS: usize = 23;

#[derive(Clone, Copy, Debug)]
pub struct Drive {
    gain: f64,
    norm: f64,
    oversample: u8,
    up_inner: HalfbandUpsampler<26>,
    down_inner: HalfbandDownsampler<INNER_TAPS>,
    up_outer: HalfbandUpsampler<12>,
    down_outer: HalfbandDownsampler<OUTER_TAPS>,
}

impl Drive {
    /// `oversample` is 1, 2 or 4. Designs its filters, so construct it before
    /// playback, not on the audio thread.
    pub fn new(gain: f64, oversample: u8) -> Self {
        assert!(matches!(oversample, 1 | 2 | 4), "oversample must be 1, 2 or 4");
        let inner = halfband_taps::<INNER_TAPS>(80.0);
        let outer = halfband_taps::<OUTER_TAPS>(80.0);
        let mut drive = Self {
            gain: 1.0,
            norm: 1.0,
            oversample,
            up_inner: HalfbandUpsampler::new(&inner),
            down_inner: HalfbandDownsampler::new(inner),
            up_outer: HalfbandUpsampler::new(&outer),
            down_outer: HalfbandDownsampler::new(outer),
        };
        drive.set_gain(gain);
        drive
    }

    /// Safe to call every control period: tanh(g x) / tanh(g) tends to x as g
    /// tends to 0, so small gains are nearly clean rather than undefined.
    pub fn set_gain(&mut self, gain: f64) {
        let gain = gain.max(1e-3);
        if gain != self.gain {
            self.gain = gain;
            self.norm = 1.0 / libm::tanh(gain);
        }
    }

    pub fn reset(&mut self) {
        self.up_inner.reset();
        self.down_inner.reset();
        self.up_outer.reset();
        self.down_outer.reset();
    }

    #[inline]
    fn shape(&self, x: f32) -> f32 {
        (libm::tanh(self.gain * x as f64) * self.norm) as f32
    }

    #[inline]
    pub fn tick(&mut self, x: f32) -> f32 {
        if self.oversample == 1 {
            return self.shape(x);
        }
        let (a, b) = self.up_inner.push(x);
        if self.oversample == 2 {
            return self.down_inner.push(self.shape(a), self.shape(b));
        }
        let (a1, a2) = self.up_outer.push(a);
        let (b1, b2) = self.up_outer.push(b);
        let c = self.down_outer.push(self.shape(a1), self.shape(a2));
        let d = self.down_outer.push(self.shape(b1), self.shape(b2));
        self.down_inner.push(c, d)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn halfband_has_exact_zeros_and_half_centre() {
        let taps = halfband_taps::<51>(80.0);
        assert_eq!(taps[25], 0.5);
        for k in (2..=25).step_by(2) {
            assert_eq!(taps[25 + k], 0.0);
            assert_eq!(taps[25 - k], 0.0);
        }
    }

    #[test]
    fn resampling_alone_passes_a_tone_unchanged() {
        // With a tiny gain tanh is a straight line, so only the filters act.
        for factor in [2u8, 4] {
            let mut d = Drive::new(1e-3, factor);
            let w = 2.0 * PI * 10_000.0 / 48_000.0;
            let out: Vec<f32> = (0..24_000).map(|n| d.tick((w * n as f64).sin() as f32)).collect();
            let tail = &out[12_000..];
            let rms = (tail.iter().map(|v| (*v as f64).powi(2)).sum::<f64>() / tail.len() as f64).sqrt();
            let db = 20.0 * (rms * 2f64.sqrt()).log10();
            assert!(db.abs() < 0.01, "{factor}x: {db} dB");
        }
    }

    #[test]
    fn full_scale_input_peaks_at_full_scale() {
        let d = Drive::new(2.5, 1);
        assert!((d.shape(1.0) - 1.0).abs() < 1e-6);
        assert!((d.shape(-1.0) + 1.0).abs() < 1e-6);
        assert_eq!(d.shape(0.0), 0.0);
    }
}
