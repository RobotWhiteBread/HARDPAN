//! The Rust port against the Python prototype, sample by sample.
//!
//! The reference samples in tests/data come from make_golden.py, which runs the
//! same cases through hardpan_proto.py. Every case must match to within a small
//! fraction of its peak. The differences that remain are rounding in places the
//! two do differently (numpy's sin and dot product against libm's and a plain
//! loop), not differences in the DSP.

use hardpan_dsp::drive::Drive;
use hardpan_dsp::effects::{JunoChorus, Reverb, TempoDelay};
use hardpan_dsp::envelope::Adsr;
use hardpan_dsp::filter::{LowPassFilter, Slope};
use hardpan_dsp::oscillator::{Oscillator, Wave};
use hardpan_dsp::sequencer::BASS_BAR;
use hardpan_dsp::voice::{MonoSynth, OscSpec, Patch, PolySynth};
use std::path::PathBuf;

const SR: f64 = 48000.0;

fn data(file: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests").join("data").join(file)
}

fn load(name: &str) -> Vec<f32> {
    let bytes = std::fs::read(data(&format!("{name}.f32"))).expect("missing reference data: run make_golden.py");
    bytes.chunks_exact(4).map(|b| f32::from_le_bytes([b[0], b[1], b[2], b[3]])).collect()
}

/// (sample, is_note_on, note, accent)
fn load_events(name: &str) -> Vec<(usize, bool, i32, bool)> {
    std::fs::read_to_string(data(&format!("{name}.events")))
        .expect("missing reference events: run make_golden.py")
        .lines()
        .map(|line| {
            let f: Vec<i64> = line.split(',').map(|v| v.trim().parse().unwrap()).collect();
            (f[0] as usize, f[1] == 1, f[2] as i32, f[3] == 1)
        })
        .collect()
}

/// Fails if any sample differs from the reference by more than `tolerance` of
/// the reference's peak.
fn compare(name: &str, rust: &[f32], tolerance: f64) {
    let reference = load(name);
    assert_eq!(rust.len(), reference.len(), "{name}: length");
    let peak = reference.iter().fold(0f64, |m, v| m.max(v.abs() as f64)).max(1e-12);
    let (at, worst) = rust
        .iter()
        .zip(&reference)
        .map(|(a, b)| (*a as f64 - *b as f64).abs())
        .enumerate()
        .fold((0, 0.0), |best, (i, d)| if d > best.1 { (i, d) } else { best });
    println!("{name:16} worst difference {:.2e} of a {peak:.3} peak ({:.1e} relative)", worst, worst / peak);
    assert!(
        worst / peak <= tolerance,
        "{name}: sample {at}: rust {} vs python {} ({:.2e} of peak)",
        rust[at],
        reference[at],
        worst / peak
    );
}

fn osc(wave: Wave, octave: i32, cents: f64, level: f32, width: f32) -> OscSpec {
    OscSpec { wave, octave, cents, level, width }
}

fn pad() -> Patch {
    Patch {
        oscillators: [
            osc(Wave::Saw, 0, -8.0, 0.45, 0.5),
            osc(Wave::Saw, 0, 8.0, 0.45, 0.5),
            osc(Wave::Square, -1, 0.0, 0.2, 0.5),
        ],
        cutoff: 550.0,
        q: 0.9,
        slope: Slope::Db24,
        env_amount: 1.5,
        keytrack: 0.4,
        filter_env: [1.5, 2.5, 0.4, 3.0],
        amp: [0.9, 1.0, 0.85, 2.5],
        gain: 0.3,
        drive: 0.0,
        oversample: 2,
        glide: 0.0,
        accent_gain: 1.0,
        accent_env: 0.0,
    }
}

fn seq_bass() -> Patch {
    Patch {
        oscillators: [
            osc(Wave::Saw, 0, -4.0, 0.5, 0.5),
            osc(Wave::Saw, 0, 4.0, 0.5, 0.5),
            osc(Wave::Square, -1, 0.0, 0.3, 0.5),
        ],
        cutoff: 200.0,
        q: 3.5,
        slope: Slope::Db24,
        env_amount: 3.5,
        keytrack: 0.5,
        filter_env: [0.0, 0.5, 0.0, 0.08],
        amp: [0.0, 0.3, 0.75, 0.06],
        gain: 0.5,
        drive: 2.5,
        oversample: 2,
        glide: 0.06,
        accent_gain: 1.6,
        accent_env: 1.2,
    }
}

fn lead() -> Patch {
    Patch {
        oscillators: [osc(Wave::Saw, 0, -6.0, 0.5, 0.5), osc(Wave::Square, 0, 6.0, 0.35, 0.4), OscSpec::OFF],
        cutoff: 1400.0,
        q: 2.0,
        slope: Slope::Db24,
        env_amount: 1.5,
        keytrack: 0.6,
        filter_env: [0.04, 0.9, 0.45, 0.6],
        amp: [0.012, 0.6, 0.85, 0.35],
        gain: 0.5,
        drive: 1.6,
        oversample: 2,
        glide: 0.11,
        accent_gain: 1.0,
        accent_env: 0.0,
    }
}

trait Synth {
    fn on(&mut self, note: i32, accent: bool);
    fn off(&mut self, note: i32);
    fn next(&mut self) -> f32;
}

impl Synth for PolySynth<8> {
    fn on(&mut self, note: i32, accent: bool) {
        self.note_on(note, accent)
    }
    fn off(&mut self, note: i32) {
        self.note_off(note)
    }
    fn next(&mut self) -> f32 {
        self.tick()
    }
}

impl Synth for MonoSynth {
    fn on(&mut self, note: i32, accent: bool) {
        self.note_on(note, accent)
    }
    fn off(&mut self, note: i32) {
        self.note_off(note)
    }
    fn next(&mut self) -> f32 {
        self.tick()
    }
}

fn run(synth: &mut impl Synth, name: &str) -> Vec<f32> {
    let events = load_events(name);
    let len = load(name).len();
    let mut i = 0;
    (0..len)
        .map(|n| {
            while i < events.len() && events[i].0 == n {
                let (_, on, note, accent) = events[i];
                if on { synth.on(note, accent) } else { synth.off(note) }
                i += 1;
            }
            synth.next()
        })
        .collect()
}

#[test]
fn oscillators_match_the_prototype() {
    for (name, wave, width) in [("sine", Wave::Sine, 0.5), ("saw", Wave::Saw, 0.5), ("square25", Wave::Square, 0.25)] {
        for hz in [110.0f32, 1000.0, 7040.0] {
            let mut o = Oscillator::new(wave, width);
            o.set_frequency(hz, SR as f32);
            let out: Vec<f32> = (0..2400).map(|_| o.tick()).collect();
            compare(&format!("osc_{name}_{}", hz as u32), &out, 1e-6);
        }
    }
}

#[test]
fn envelope_matches_the_prototype() {
    let mut env = Adsr::new(0.01, 0.15, 0.7, 0.3, SR);
    env.note_on();
    let mut out: Vec<f32> = (0..19200).map(|_| env.tick()).collect();
    env.note_off();
    out.extend((0..19200).map(|_| env.tick()));
    compare("adsr", &out, 1e-6);
}

#[test]
fn filters_match_the_prototype() {
    for (name, slope, cutoff, q) in [("filter24", Slope::Db24, 500.0, 3.0), ("filter12", Slope::Db12, 2000.0, 0.7071)] {
        let mut f = LowPassFilter::new(slope, SR);
        f.set(cutoff, q);
        let out: Vec<f32> = (0..4800).map(|n| f.tick(if n == 0 { 1.0 } else { 0.0 })).collect();
        compare(name, &out, 1e-5);
    }
}

#[test]
fn drive_matches_the_prototype() {
    for factor in [2u8, 4] {
        let mut o = Oscillator::new(Wave::Saw, 0.5);
        o.set_frequency(110.0, SR as f32);
        let mut d = Drive::new(2.5, factor);
        let out: Vec<f32> = (0..4800).map(|_| d.tick(o.tick())).collect();
        compare(&format!("drive{factor}x"), &out, 1e-5);
    }
}

#[test]
fn effects_match_the_prototype() {
    let input = load("fx_input");

    let mut chorus = JunoChorus::new(vec![0.0; JunoChorus::<Vec<f32>>::required_len(SR)], SR, 0.5, 3.5, 1.8, 0.7);
    let out: Vec<f32> = input[..24000].iter().flat_map(|&x| <[f32; 2]>::from(chorus.tick(x))).collect();
    compare("fx_chorus", &out, 1e-5);

    let len = TempoDelay::<Vec<f32>>::required_len(SR);
    let mut delay = TempoDelay::new(vec![0.0; len], vec![0.0; len], SR, 0.75 * 60.0 / 110.0, 0.45, 2500.0);
    let out: Vec<f32> = input.iter().flat_map(|&x| <[f32; 2]>::from(delay.tick(x))).collect();
    compare("fx_delay", &out, 1e-5);

    let mut reverb = Reverb::new(vec![0.0; Reverb::<Vec<f32>>::required_len(SR)], SR, 0.88, 0.5);
    let out: Vec<f32> = input.iter().flat_map(|&x| <[f32; 2]>::from(reverb.tick(x))).collect();
    compare("fx_reverb", &out, 1e-5);
}

#[test]
fn pad_with_voice_stealing_matches_the_prototype() {
    let mut synth: PolySynth<8> = PolySynth::new(&pad(), SR);
    let out = run(&mut synth, "pad");
    compare("pad", &out, 1e-4);
}

#[test]
fn sequenced_bass_with_drive_accent_and_glide_matches_the_prototype() {
    assert_eq!(BASS_BAR.len(), 16);
    let mut synth = MonoSynth::new(&seq_bass(), SR);
    let out = run(&mut synth, "seq_bass");
    compare("seq_bass", &out, 1e-4);
}

#[test]
fn gliding_lead_matches_the_prototype() {
    let mut synth = MonoSynth::new(&lead(), SR);
    let out = run(&mut synth, "lead");
    compare("lead", &out, 1e-4);
}
