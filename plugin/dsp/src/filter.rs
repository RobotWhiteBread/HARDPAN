//! The Audio EQ Cookbook low-pass biquad in direct form I, and the 12/24 dB
//! resonant filter built from one or two of them. See `Biquad` and
//! `LowPassFilter` in `hardpan_proto.py` for the reasoning.

use core::f64::consts::PI;

/// Anything quieter than this is flushed to exactly zero, keeping the arithmetic
/// off the slow denormal path. On the audio thread that slowdown is real, which is
/// why this matters far more here than in the Python prototype.
pub const FLUSH_BELOW: f32 = 1e-20;
/// The two section Qs of a fourth-order Butterworth low-pass.
pub const BUTTERWORTH_Q: (f64, f64) = (0.5412, 1.3066);
pub const MIN_CUTOFF: f64 = 20.0;
pub const MIN_Q: f64 = 0.5;
pub const MAX_Q: f64 = 20.0;

#[derive(Clone, Copy, Debug)]
pub struct Biquad {
    pub b0: f32,
    pub b1: f32,
    pub b2: f32,
    pub a1: f32,
    pub a2: f32,
    x1: f32,
    x2: f32,
    y1: f32,
    y2: f32,
    sample_rate: f64,
}

impl Biquad {
    pub fn new(sample_rate: f64) -> Self {
        Self { b0: 0.0, b1: 0.0, b2: 0.0, a1: 0.0, a2: 0.0, x1: 0.0, x2: 0.0, y1: 0.0, y2: 0.0, sample_rate }
    }

    pub fn reset(&mut self) {
        self.x1 = 0.0;
        self.x2 = 0.0;
        self.y1 = 0.0;
        self.y2 = 0.0;
    }

    /// Cookbook low-pass coefficients, computed in f64 and rounded to f32 once.
    pub fn set_lowpass(&mut self, cutoff: f64, q: f64) {
        let w0 = 2.0 * PI * cutoff / self.sample_rate;
        let cos_w0 = libm::cos(w0);
        let alpha = libm::sin(w0) / (2.0 * q);
        let a0 = 1.0 + alpha;
        let b1 = (1.0 - cos_w0) / a0;
        self.b0 = (0.5 * b1) as f32;
        self.b2 = self.b0;
        self.b1 = b1 as f32;
        self.a1 = (-2.0 * cos_w0 / a0) as f32;
        self.a2 = ((1.0 - alpha) / a0) as f32;
    }

    #[inline]
    pub fn tick(&mut self, x: f32) -> f32 {
        let mut y = self.b0 * x + self.b1 * self.x1 + self.b2 * self.x2 - self.a1 * self.y1 - self.a2 * self.y2;
        if -FLUSH_BELOW < y && y < FLUSH_BELOW {
            y = 0.0;
        }
        self.x2 = self.x1;
        self.x1 = x;
        self.y2 = self.y1;
        self.y1 = y;
        y
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Slope {
    Db12,
    Db24,
}

#[derive(Clone, Copy, Debug)]
pub struct LowPassFilter {
    slope: Slope,
    sections: [Biquad; 2],
    max_cutoff: f64,
    cutoff: f64,
    q: f64,
}

impl LowPassFilter {
    pub fn new(slope: Slope, sample_rate: f64) -> Self {
        Self {
            slope,
            sections: [Biquad::new(sample_rate); 2],
            max_cutoff: 0.45 * sample_rate,
            cutoff: f64::NAN,
            q: f64::NAN,
        }
    }

    pub fn reset(&mut self) {
        for section in &mut self.sections {
            section.reset();
        }
    }

    pub fn slope(&self) -> Slope {
        self.slope
    }

    /// Switching slope mid-note changes the filter's structure; the second
    /// section starts from silence, so expect a small bump.
    pub fn set_slope(&mut self, slope: Slope) {
        if slope != self.slope {
            self.slope = slope;
            self.sections[1].reset();
            self.cutoff = f64::NAN; // recompute on the next set()
        }
    }

    pub fn set(&mut self, cutoff: f64, q: f64) {
        let cutoff = cutoff.max(MIN_CUTOFF).min(self.max_cutoff);
        let q = q.max(MIN_Q).min(MAX_Q);
        if cutoff == self.cutoff && q == self.q {
            return;
        }
        self.cutoff = cutoff;
        self.q = q;
        match self.slope {
            Slope::Db12 => self.sections[0].set_lowpass(cutoff, q),
            Slope::Db24 => {
                // Butterworth pair, resonance in the second section only, scaled so
                // the gain at the cutoff equals q in both modes.
                self.sections[0].set_lowpass(cutoff, BUTTERWORTH_Q.0);
                self.sections[1].set_lowpass(cutoff, BUTTERWORTH_Q.1 * q / libm::sqrt(0.5));
            }
        }
    }

    #[inline]
    pub fn tick(&mut self, x: f32) -> f32 {
        let y = self.sections[0].tick(x);
        match self.slope {
            Slope::Db12 => y,
            Slope::Db24 => self.sections[1].tick(y),
        }
    }

    pub fn sections(&self) -> &[Biquad; 2] {
        &self.sections
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn response_db(filter: &LowPassFilter, f: f64, sample_rate: f64) -> f64 {
        let w = 2.0 * PI * f / sample_rate;
        let (re1, im1) = (w.cos(), -w.sin());
        let (re2, im2) = ((2.0 * w).cos(), -(2.0 * w).sin());
        let count = if filter.slope == Slope::Db12 { 1 } else { 2 };
        let mut mag = 1.0;
        for s in &filter.sections[..count] {
            let num = (s.b0 as f64 + s.b1 as f64 * re1 + s.b2 as f64 * re2, s.b1 as f64 * im1 + s.b2 as f64 * im2);
            let den = (1.0 + s.a1 as f64 * re1 + s.a2 as f64 * re2, s.a1 as f64 * im1 + s.a2 as f64 * im2);
            mag *= (num.0.hypot(num.1)) / (den.0.hypot(den.1));
        }
        20.0 * mag.log10()
    }

    #[test]
    fn minus_3db_at_the_cutoff_in_both_slopes() {
        for slope in [Slope::Db12, Slope::Db24] {
            let mut f = LowPassFilter::new(slope, 48000.0);
            f.set(1000.0, core::f64::consts::FRAC_1_SQRT_2);
            let db = response_db(&f, 1000.0, 48000.0);
            assert!((db + 3.01).abs() < 0.05, "{slope:?}: {db}");
        }
    }

    #[test]
    fn gain_at_the_cutoff_equals_q() {
        for slope in [Slope::Db12, Slope::Db24] {
            let mut f = LowPassFilter::new(slope, 48000.0);
            f.set(1000.0, 4.0);
            let db = response_db(&f, 1000.0, 48000.0);
            assert!((db - 20.0 * 4f64.log10()).abs() < 0.05, "{slope:?}: {db}");
        }
    }

    #[test]
    fn impulse_response_flushes_to_exact_zero() {
        let mut f = LowPassFilter::new(Slope::Db24, 48000.0);
        f.set(200.0, 5.0);
        f.tick(1.0);
        let mut last_nonzero = 0;
        for n in 0..(20 * 48000) {
            let y = f.tick(0.0);
            assert!(y == 0.0 || y.abs() >= f32::MIN_POSITIVE, "denormal at {n}");
            if y != 0.0 {
                last_nonzero = n;
            }
        }
        assert!(last_nonzero < 5 * 48000, "still ringing at {last_nonzero}");
    }
}
