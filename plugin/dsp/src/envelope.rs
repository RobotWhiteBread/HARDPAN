//! The ADSR envelope from the prototype: every stage is an exponential approach
//! toward a target past its goal, tracked as the distance to the target so f32
//! rounding cannot stall it. Note-on and note-off change the stage, never the level.

pub const MIN_STAGE_SECONDS: f64 = 0.005;
pub const ATTACK_OVERSHOOT: f64 = 0.3;
pub const DECAY_OVERSHOOT: f64 = 1e-4;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Stage {
    Idle,
    Attack,
    Decay,
    Sustain,
    Release,
}

#[derive(Clone, Copy, Debug)]
pub struct Adsr {
    pub level: f32,
    pub stage: Stage,
    distance: f32,
    sustain: f32,
    attack_target: f32,
    decay_target: f32,
    release_target: f32,
    attack_coef: f32,
    decay_coef: f32,
    release_coef: f32,
    times: [f64; 4],
    sample_rate: f64,
}

/// Starting at 0 and aiming at 1 + overshoot, the level crosses 1 after exactly
/// `seconds` when c**n = overshoot / (1 + overshoot). Computed in f64 and rounded
/// once, because for long stages c sits very close to 1.
fn coefficient(seconds: f64, overshoot: f64, sample_rate: f64) -> f32 {
    let n = seconds.max(MIN_STAGE_SECONDS) * sample_rate;
    libm::exp(libm::log(overshoot / (1.0 + overshoot)) / n) as f32
}

impl Adsr {
    /// Times in seconds; sustain is a level from 0 to 1.
    pub fn new(attack: f64, decay: f64, sustain: f64, release: f64, sample_rate: f64) -> Self {
        let mut env = Self {
            level: 0.0,
            stage: Stage::Idle,
            distance: 0.0,
            sustain: 0.0,
            attack_target: (1.0 + ATTACK_OVERSHOOT) as f32,
            decay_target: 0.0,
            release_target: (-DECAY_OVERSHOOT) as f32,
            attack_coef: 0.0,
            decay_coef: 0.0,
            release_coef: 0.0,
            times: [f64::NAN; 4],
            sample_rate,
        };
        env.set(attack, decay, sustain, release);
        env
    }

    /// Change the times and sustain level, including while a note sounds.
    ///
    /// The prototype only set these between notes. In a plugin the user can turn
    /// them at any moment, so every stage re-measures its distance from its own
    /// (possibly moved) target, and a held sustain follows the new level. The
    /// caller smooths `sustain`, so that follow is a glide, not a jump.
    pub fn set(&mut self, attack: f64, decay: f64, sustain: f64, release: f64) {
        let sustain = sustain.clamp(0.0, 1.0);
        let new_times = [attack, decay, sustain, release];
        if new_times == self.times {
            return; // nothing moved: skip the exp and log
        }
        if attack != self.times[0] {
            self.attack_coef = coefficient(attack, ATTACK_OVERSHOOT, self.sample_rate);
        }
        if decay != self.times[1] {
            self.decay_coef = coefficient(decay, DECAY_OVERSHOOT, self.sample_rate);
        }
        if release != self.times[3] {
            self.release_coef = coefficient(release, DECAY_OVERSHOOT, self.sample_rate);
        }
        self.sustain = sustain as f32;
        self.decay_target = (sustain - DECAY_OVERSHOOT * (1.0 - sustain)) as f32;
        self.times = new_times;
        match self.stage {
            Stage::Decay => {
                // A sustain raised past where the decay has reached ends the decay.
                // The caller smooths sustain, so at the crossing the two are
                // within one smoothing step and taking the new value is no jump.
                if self.level <= self.sustain {
                    self.stage = Stage::Sustain;
                    self.level = self.sustain;
                } else {
                    self.distance = self.level - self.decay_target;
                }
            }
            Stage::Sustain => self.level = self.sustain,
            _ => {}
        }
    }

    pub fn active(&self) -> bool {
        self.stage != Stage::Idle
    }

    pub fn note_on(&mut self) {
        self.begin(Stage::Attack, self.attack_target);
    }

    pub fn note_off(&mut self) {
        if self.stage != Stage::Idle {
            self.begin(Stage::Release, self.release_target);
        }
    }

    /// Drop to silence. Only for a voice that has already been faded out.
    pub fn reset(&mut self) {
        self.level = 0.0;
        self.distance = 0.0;
        self.stage = Stage::Idle;
    }

    fn begin(&mut self, stage: Stage, target: f32) {
        self.stage = stage;
        self.distance = self.level - target;
    }

    /// The largest change between two consecutive samples this envelope can make.
    pub fn max_step(&self) -> f32 {
        let attack = (1.0 + ATTACK_OVERSHOOT as f32) * (1.0 - self.attack_coef);
        let decay = (1.0 - self.sustain) * (1.0 + DECAY_OVERSHOOT as f32) * (1.0 - self.decay_coef);
        let release = (1.0 + DECAY_OVERSHOOT as f32) * (1.0 - self.release_coef);
        attack.max(decay).max(release)
    }

    #[inline]
    pub fn tick(&mut self) -> f32 {
        match self.stage {
            Stage::Attack => {
                self.distance *= self.attack_coef;
                self.level = self.attack_target + self.distance;
                if self.level >= 1.0 {
                    self.level = 1.0;
                    self.begin(Stage::Decay, self.decay_target);
                }
            }
            Stage::Decay => {
                self.distance *= self.decay_coef;
                self.level = self.decay_target + self.distance;
                if self.level <= self.sustain {
                    self.level = self.sustain;
                    self.stage = Stage::Sustain;
                }
            }
            Stage::Release => {
                self.distance *= self.release_coef;
                self.level = self.release_target + self.distance;
                if self.level <= 0.0 {
                    self.level = 0.0;
                    self.stage = Stage::Idle;
                }
            }
            Stage::Idle | Stage::Sustain => {}
        }
        self.level
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SR: f64 = 48000.0;

    fn run(env: &mut Adsr, n: usize) -> Vec<f32> {
        (0..n).map(|_| env.tick()).collect()
    }

    #[test]
    fn attack_reaches_full_level_after_its_time() {
        let mut env = Adsr::new(0.010, 0.150, 0.7, 0.300, SR);
        env.note_on();
        let out = run(&mut env, 1000);
        let peak = out.iter().position(|&v| v == 1.0).unwrap();
        assert!((479..=481).contains(&peak), "1.0 first at sample {peak}");
        assert!(out[..peak].iter().all(|&v| v < 1.0 && v > 0.0));
        // The first sample of an attack from silence is (1 + 0.3) * (1 - c).
        let c = coefficient(0.010, ATTACK_OVERSHOOT, SR);
        assert_eq!(out[0], 1.3f32 + (0.0 - 1.3f32) * c);
    }

    #[test]
    fn decay_lands_exactly_on_sustain_and_holds() {
        let mut env = Adsr::new(0.010, 0.150, 0.7, 0.300, SR);
        env.note_on();
        let out = run(&mut env, 48000);
        let reached = out.iter().position(|&v| v == 0.7f32).unwrap();
        assert!((7677..=7683).contains(&reached), "sustain from sample {reached}");
        assert!(out[reached..].iter().all(|&v| v == 0.7f32));
        assert_eq!(env.stage, Stage::Sustain);
    }

    #[test]
    fn long_decays_do_not_stall_above_sustain() {
        for decay in [0.25, 1.0, 3.0] {
            let mut env = Adsr::new(0.010, decay, 0.7, 0.3, SR);
            env.note_on();
            run(&mut env, ((0.010 + decay) * SR * 1.01) as usize + 10);
            assert_eq!(env.stage, Stage::Sustain, "decay {decay}");
        }
    }

    #[test]
    fn release_ends_at_exactly_zero() {
        let mut env = Adsr::new(0.005, 0.1, 1.0, 0.2, SR);
        env.note_on();
        run(&mut env, 2000);
        env.note_off();
        let out = run(&mut env, 48000);
        let silent = out.iter().position(|&v| v == 0.0).unwrap();
        assert!((9598..=9602).contains(&silent), "silent from sample {silent}");
        assert_eq!(env.stage, Stage::Idle);
        assert!(out[silent..].iter().all(|&v| v == 0.0));
    }

    #[test]
    fn note_off_during_attack_fades_from_where_it_is() {
        let mut env = Adsr::new(0.010, 0.150, 0.7, 0.300, SR);
        env.note_on();
        run(&mut env, 200);
        let before = env.level;
        env.note_off();
        let after = env.tick();
        assert!(after < before && before - after <= env.max_step(), "{before} -> {after}");
    }

    #[test]
    fn retrigger_during_release_rises_from_where_it_is() {
        let mut env = Adsr::new(0.010, 0.150, 0.7, 0.300, SR);
        env.note_on();
        run(&mut env, 10000);
        env.note_off();
        run(&mut env, 2000);
        let before = env.level;
        env.note_on();
        let after = env.tick();
        assert!(after > before && after - before <= env.max_step(), "{before} -> {after}");
    }

    #[test]
    fn never_steps_more_than_max_step() {
        // Pseudo-random note-ons and note-offs with a tiny LCG (no rand crate needed).
        let mut seed: u32 = 12345;
        let mut next = move || {
            seed = seed.wrapping_mul(1664525).wrapping_add(1013904223);
            seed
        };
        let mut env = Adsr::new(0.0, 0.05, 0.4, 0.0, SR);
        let mut last = 0.0f32;
        for _ in 0..300 {
            if next() % 2 == 0 { env.note_on() } else { env.note_off() }
            for _ in 0..(next() % 2000 + 1) {
                let v = env.tick();
                assert!((v - last).abs() <= env.max_step() + 1e-7);
                assert!((0.0..=1.0).contains(&v));
                last = v;
            }
        }
    }

    #[test]
    fn sustain_follows_a_moving_sustain_parameter() {
        let mut env = Adsr::new(0.005, 0.05, 0.7, 0.3, SR);
        env.note_on();
        run(&mut env, 10000);
        env.set(0.005, 0.05, 0.6, 0.3);
        assert_eq!(env.tick(), 0.6f32);
        env.set(0.005, 0.05, 0.65, 0.3);
        assert_eq!(env.tick(), 0.65f32);
    }
}
