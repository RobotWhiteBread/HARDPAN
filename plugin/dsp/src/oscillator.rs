//! Phase-accumulator oscillators: sine, PolyBLEP saw, PolyBLEP pulse.
//! See `PhaseOscillator`, `poly_blep` and the BLEP note in `hardpan_proto.py`.

/// 2 pi rounded to f32, as the prototype's `TWO_PI`.
pub const TWO_PI: f32 = (2.0 * core::f64::consts::PI) as f32;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Wave {
    Sine,
    Saw,
    /// A pulse wave; `Oscillator::width` is the fraction of the cycle spent high.
    Square,
}

/// Phase in cycles, 0 to 1, advanced by `increment` (frequency / sample rate) per
/// sample and wrapped by subtracting 1, which is exact for a phase between 1 and 2.
#[derive(Clone, Copy, Debug, Default)]
pub struct Phase {
    pub phase: f32,
    pub increment: f32,
}

impl Phase {
    pub fn set_frequency(&mut self, hz: f32, sample_rate: f32) {
        self.increment = hz / sample_rate;
    }

    pub fn reset(&mut self) {
        self.phase = 0.0;
    }

    #[inline]
    pub fn advance(&mut self) {
        self.phase += self.increment;
        if self.phase >= 1.0 {
            self.phase -= 1.0;
        }
    }
}

/// The PolyBLEP correction for an upward jump of 2 at phase 0, sampled at phase
/// `t` with a phase step of `dt` per sample. Nonzero only within one sample of
/// the jump.
#[inline]
pub fn poly_blep(t: f32, dt: f32) -> f32 {
    if t < dt {
        let x = t / dt;
        return x + x - x * x - 1.0;
    }
    if t > 1.0 - dt {
        let x = (t - 1.0) / dt;
        return x * x + x + x + 1.0;
    }
    0.0
}

#[derive(Clone, Copy, Debug)]
pub struct Oscillator {
    pub phase: Phase,
    pub wave: Wave,
    pub width: f32,
}

impl Oscillator {
    pub fn new(wave: Wave, width: f32) -> Self {
        Self { phase: Phase::default(), wave, width }
    }

    pub fn set_frequency(&mut self, hz: f32, sample_rate: f32) {
        self.phase.set_frequency(hz, sample_rate);
    }

    /// One sample. Output comes from the current phase, then the phase advances,
    /// so a note started at phase 0 really starts at phase 0.
    #[inline]
    pub fn tick(&mut self) -> f32 {
        let t = self.phase.phase;
        let dt = self.phase.increment;
        let out = match self.wave {
            Wave::Sine => libm::sinf(TWO_PI * t),
            // The saw drops by 2 at the wrap, so its correction is subtracted.
            Wave::Saw => 2.0 * t - 1.0 - poly_blep(t, dt),
            Wave::Square => {
                let width = self.width;
                let high_or_low = if t < width { 1.0 } else { -1.0 };
                // Up by 2 at phase 0, down by 2 at phase `width`.
                let since_drop = if t >= width { t - width } else { t - width + 1.0 };
                high_or_low + poly_blep(t, dt) - poly_blep(since_drop, dt)
            }
        };
        self.phase.advance();
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SR: f32 = 48000.0;

    fn render(wave: Wave, width: f32, hz: f32, n: usize) -> Vec<f32> {
        let mut osc = Oscillator::new(wave, width);
        osc.set_frequency(hz, SR);
        (0..n).map(|_| osc.tick()).collect()
    }

    #[test]
    fn sine_matches_the_formula_sample_by_sample() {
        // A phase accumulator should give sin(2 pi f n / fs), up to f32 rounding of
        // the phase. Each addition rounds to the nearest f32, about 3e-8 of a cycle
        // at worst, and over a second that drifts by a few ten-thousandths of a
        // cycle (the prototype measured A4 at 439.9997 Hz). Hence 2e-3, not 1e-6.
        let out = render(Wave::Sine, 0.5, 440.0, 48000);
        for (n, &y) in out.iter().enumerate() {
            let exact = (2.0 * core::f64::consts::PI * 440.0 * n as f64 / 48000.0).sin();
            assert!((y as f64 - exact).abs() < 2e-3, "sample {n}: {y} vs {exact}");
        }
        assert_eq!(out[0], 0.0);
    }

    #[test]
    fn first_sine_samples() {
        // 1 kHz at 48 kHz: 48 samples per cycle, so sample 12 is a quarter cycle.
        let out = render(Wave::Sine, 0.5, 1000.0, 25);
        assert_eq!(out[0], 0.0);
        assert!((out[12] - 1.0).abs() < 1e-6, "{}", out[12]);
        assert!(out[24].abs() < 1e-5, "{}", out[24]);
    }

    #[test]
    fn saw_is_the_phase_rescaled_away_from_the_jump() {
        // 750 Hz: phase step exactly 1/64, so the values are exact in f32.
        let out = render(Wave::Saw, 0.5, 750.0, 64);
        assert_eq!(out[16], 2.0 * 0.25 - 1.0);
        assert_eq!(out[32], 0.0);
        assert_eq!(out[48], 0.5);
    }

    #[test]
    fn polyblep_saw_splits_the_drop_into_two_half_steps() {
        // At phase 0 (the jump) the corrected saw sits at the midpoint of the drop.
        let out = render(Wave::Saw, 0.5, 750.0, 65);
        assert_eq!(out[0], 0.0, "the sample on the jump is its midpoint");
        // The sample just before the wrap, phase 63/64: x = -1 in the polynomial,
        // which gives no correction... one step later we are on the jump.
        assert_eq!(out[63], 2.0 * (63.0 / 64.0) - 1.0);
        assert_eq!(out[64], 0.0);
    }

    #[test]
    fn polyblep_correction_values() {
        assert_eq!(poly_blep(0.5, 0.1), 0.0);
        // Right on the jump the correction is -1, half of the jump of 2.
        assert_eq!(poly_blep(0.0, 0.1), -1.0);
        // Half a sample after: x = 0.5 -> 2(0.5) - 0.25 - 1 = -0.25.
        assert_eq!(poly_blep(0.05, 0.1), -0.25);
        // Half a sample before: x = -0.5 -> 0.25 - 1 + 1 = 0.25.
        assert!((poly_blep(0.95, 0.1) - 0.25).abs() < 1e-6);
    }

    #[test]
    fn square_holds_its_levels_and_duty_cycle() {
        let out = render(Wave::Square, 0.25, 750.0, 64 * 10);
        let mean: f32 = out.iter().sum::<f32>() / out.len() as f32;
        assert!((mean - (2.0 * 0.25 - 1.0)).abs() < 1e-4, "mean {mean}");
        assert_eq!(out[8], 1.0); // well inside the high quarter
        assert_eq!(out[40], -1.0); // well inside the low part
    }

    #[test]
    fn phase_stays_wrapped() {
        let mut p = Phase::default();
        p.set_frequency(12345.0, SR);
        for _ in 0..1_000_000 {
            p.advance();
            assert!((0.0..1.0).contains(&p.phase));
        }
    }
}
