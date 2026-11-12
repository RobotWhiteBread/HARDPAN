//! HARDPAN as a CLAP and VST3 instrument, built on nice-plug.
//!
//! This crate is only the wrapper: parameters, MIDI, the host's tempo, and the
//! routing between the pieces. All of the signal processing is in the
//! `hardpan-dsp` crate, which is `no_std`, allocation-free, and tested sample for
//! sample against the Python prototype.
//!
//! Audio-thread rules this file keeps:
//! - No allocation in `process`. Every buffer is made in `activate`, before
//!   playback, and the `assert_process_allocs` feature checks this in debug builds.
//! - No locks. Parameters are atomics owned by nice-plug; nothing else is shared.
//! - Everything a user can turn is smoothed, so turning it does not click.

use hardpan_dsp::effects::{JunoChorus, Reverb, TempoDelay};
use hardpan_dsp::filter::Slope;
use hardpan_dsp::oscillator::Wave;
use hardpan_dsp::sequencer::{BASS_BAR, SeqEvent, Sequencer};
use hardpan_dsp::voice::{CONTROL_INTERVAL, MonoSynth, OscSpec, Patch, PolySynth};
use nice_plug::prelude::*;
use std::sync::Arc;

/// Notes at or above this velocity are accented, like an accented sequencer step.
const ACCENT_VELOCITY: f32 = 0.8;
/// Output of the voices before the master volume, leaving headroom for chords,
/// resonance and drive.
const VOICE_GAIN: f32 = 0.35;
/// Eight voices stack up: a ten-note chord on the default driven, resonant patch
/// peaked at 1.86 at full voice gain. Poly mode plays each voice 6 dB quieter.
const POLY_GAIN: f32 = 0.5;
/// Measured: the reverb's output is 0.177 of its input level on steady sound, so
/// this makes a reverb mix of 1.0 come back at the same loudness that went in.
const REVERB_RETURN: f32 = 5.64;
const FALLBACK_BPM: f64 = 110.0;

/// The pitch strip bends this many semitones each way, the usual keyboard default.
const BEND_RANGE: f64 = 2.0;
/// The mod strip opens the filter by up to this many octaves.
const MOD_OCTAVES: f64 = 3.0;
const MOD_CC: u8 = 1;
const SUSTAIN_CC: u8 = 64;
const ALL_SOUND_OFF_CC: u8 = 120;
const ALL_NOTES_OFF_CC: u8 = 123;
/// How fast a knob's value is followed. A MIDI knob has only 128 positions, so
/// its steps are smoothed over the same 20 ms as the host's own parameters.
const KNOB_SMOOTHING_SECONDS: f64 = 0.02;
/// Short enough that a bend feels immediate, long enough to hide the strip's steps.
const BEND_SMOOTHING_SECONDS: f64 = 0.005;

/// A setting that a knob or slider on a MIDI controller can take over.
#[derive(Debug, Clone, Copy, PartialEq)]
enum Target {
    Cutoff,
    Resonance,
    EnvAmount,
    Drive,
    ChorusMix,
    DelayMix,
    DelayFeedback,
    ReverbMix,
    FilterDecay,
    AmpAttack,
    AmpRelease,
    Glide,
}
const TARGETS: usize = 12;

/// Which MIDI CC moves which setting. The numbers are what an Arturia MiniLab 3
/// sends from a factory User program: knobs 1 to 8, then sliders 1 to 4. (Its
/// "Arturia" and "DAWs" programs are not documented to send these; Shift + Pad 3
/// switches program.) A different controller only needs different numbers here.
const CC_MAP: [(u8, Target); TARGETS] = [
    (74, Target::Cutoff),
    (71, Target::Resonance),
    (76, Target::EnvAmount),
    (77, Target::Drive),
    (93, Target::ChorusMix),
    (18, Target::DelayMix),
    (19, Target::DelayFeedback),
    (16, Target::ReverbMix),
    (82, Target::FilterDecay),
    (83, Target::AmpAttack),
    (85, Target::AmpRelease),
    (17, Target::Glide),
];

/// One controller knob standing in for one parameter.
///
/// A plugin cannot move its own parameters from the audio thread; only the host
/// and an editor window can. So a knob does not change the parameter. It takes
/// over from it: from the first turn the synth follows the knob, and it keeps
/// following until the parameter itself is changed in the host, which hands
/// control back. The host's slider does not move while a knob is in charge.
#[derive(Debug, Clone, Copy)]
struct Knob {
    active: bool,
    /// Where the knob is, 0 to 1 across the parameter's range.
    target: f32,
    /// The smoothed position the synth is using.
    current: f32,
    /// The parameter's own position when the knob took over, to notice it changing.
    seen: f32,
}

impl Knob {
    const IDLE: Knob = Knob { active: false, target: 0.0, current: 0.0, seen: 0.0 };

    fn turn(&mut self, param: &FloatParam, position: f32) {
        if !self.active {
            // Start from the parameter's value, so the first turn glides there
            // instead of jumping.
            self.seen = param.unmodulated_normalized_value();
            self.current = self.seen;
            self.active = true;
        }
        self.target = position.clamp(0.0, 1.0);
    }

    /// The value to use this step: the knob's if it is in charge, otherwise
    /// `host`, the parameter's own value.
    fn value(&mut self, param: &FloatParam, host: f32, smoothing: f32) -> f32 {
        if !self.active {
            return host;
        }
        if param.unmodulated_normalized_value() != self.seen {
            self.active = false;
            return host;
        }
        self.current += (self.target - self.current) * smoothing;
        param.preview_plain(self.current)
    }
}

#[derive(Enum, Debug, Clone, Copy, PartialEq)]
enum Mode {
    #[name = "Poly (8 voices)"]
    Poly,
    #[name = "Mono (glide)"]
    Mono,
}

#[derive(Enum, Debug, Clone, Copy, PartialEq)]
enum WaveChoice {
    Sine,
    Saw,
    #[name = "Square / pulse"]
    Square,
}

impl From<WaveChoice> for Wave {
    fn from(w: WaveChoice) -> Self {
        match w {
            WaveChoice::Sine => Wave::Sine,
            WaveChoice::Saw => Wave::Saw,
            WaveChoice::Square => Wave::Square,
        }
    }
}

#[derive(Enum, Debug, Clone, Copy, PartialEq)]
enum SlopeChoice {
    #[name = "12 dB/oct"]
    Db12,
    #[name = "24 dB/oct"]
    Db24,
}

#[derive(Enum, Debug, Clone, Copy, PartialEq)]
enum DelayTime {
    #[name = "1/16"]
    Sixteenth,
    #[name = "1/8"]
    Eighth,
    #[name = "Dotted 1/8"]
    DottedEighth,
    #[name = "1/4"]
    Quarter,
    #[name = "Dotted 1/4"]
    DottedQuarter,
    #[name = "1/2"]
    Half,
}

impl DelayTime {
    fn beats(self) -> f64 {
        match self {
            DelayTime::Sixteenth => 0.25,
            DelayTime::Eighth => 0.5,
            DelayTime::DottedEighth => 0.75,
            DelayTime::Quarter => 1.0,
            DelayTime::DottedQuarter => 1.5,
            DelayTime::Half => 2.0,
        }
    }
}

fn seconds(name: &str, default: f32, max: f32) -> FloatParam {
    FloatParam::new(name, default, FloatRange::Skewed { min: 0.0, max, factor: FloatRange::skew_factor(-2.0) })
        .with_unit(" s")
        .with_value_to_string(formatters::v2s_f32_rounded(3))
}

fn amount(name: &str, default: f32) -> FloatParam {
    FloatParam::new(name, default, FloatRange::Linear { min: 0.0, max: 1.0 })
        .with_smoother(SmoothingStyle::Linear(20.0))
        .with_value_to_string(formatters::v2s_f32_rounded(2))
}

#[derive(Params)]
struct OscParams {
    #[id = "wave"]
    wave: EnumParam<WaveChoice>,
    #[id = "octave"]
    octave: IntParam,
    #[id = "cents"]
    cents: FloatParam,
    #[id = "level"]
    level: FloatParam,
    #[id = "width"]
    width: FloatParam,
}

impl OscParams {
    fn new(wave: WaveChoice, octave: i32, cents: f32, level: f32) -> Self {
        Self {
            wave: EnumParam::new("Wave", wave),
            octave: IntParam::new("Octave", octave, IntRange::Linear { min: -2, max: 2 }),
            cents: FloatParam::new("Detune", cents, FloatRange::Linear { min: -50.0, max: 50.0 })
                .with_smoother(SmoothingStyle::Linear(20.0))
                .with_unit(" cents")
                .with_value_to_string(formatters::v2s_f32_rounded(1)),
            level: amount("Level", level),
            width: FloatParam::new("Pulse width", 0.5, FloatRange::Linear { min: 0.05, max: 0.95 })
                .with_smoother(SmoothingStyle::Linear(20.0))
                .with_value_to_string(formatters::v2s_f32_rounded(2)),
        }
    }
}

#[derive(Params)]
struct EnvParams {
    #[id = "attack"]
    attack: FloatParam,
    #[id = "decay"]
    decay: FloatParam,
    #[id = "sustain"]
    sustain: FloatParam,
    #[id = "release"]
    release: FloatParam,
}

impl EnvParams {
    fn new(attack: f32, decay: f32, sustain: f32, release: f32) -> Self {
        Self {
            attack: seconds("Attack", attack, 5.0),
            decay: seconds("Decay", decay, 10.0),
            sustain: amount("Sustain", sustain),
            release: seconds("Release", release, 10.0),
        }
    }

    fn values(&self, steps: u32) -> [f64; 4] {
        [
            self.attack.value() as f64,
            self.decay.value() as f64,
            self.sustain.smoothed.next_step(steps) as f64,
            self.release.value() as f64,
        ]
    }
}

#[derive(Params)]
struct HardpanParams {
    #[id = "mode"]
    mode: EnumParam<Mode>,
    #[nested(array, group = "Oscillator")]
    oscillators: [OscParams; 3],
    #[id = "drive"]
    drive: FloatParam,
    #[id = "cutoff"]
    cutoff: FloatParam,
    #[id = "resonance"]
    resonance: FloatParam,
    #[id = "slope"]
    slope: EnumParam<SlopeChoice>,
    #[id = "env_amount"]
    env_amount: FloatParam,
    #[id = "keytrack"]
    keytrack: FloatParam,
    #[nested(id_prefix = "fenv", group = "Filter envelope")]
    filter_env: EnvParams,
    #[nested(id_prefix = "aenv", group = "Amp envelope")]
    amp_env: EnvParams,
    #[id = "glide"]
    glide: FloatParam,
    #[id = "accent_gain"]
    accent_gain: FloatParam,
    #[id = "accent_filter"]
    accent_filter: FloatParam,
    #[id = "seq_on"]
    seq_on: BoolParam,
    #[id = "swing"]
    swing: FloatParam,
    #[id = "chorus_mix"]
    chorus_mix: FloatParam,
    #[id = "chorus_rate"]
    chorus_rate: FloatParam,
    #[id = "chorus_depth"]
    chorus_depth: FloatParam,
    #[id = "delay_mix"]
    delay_mix: FloatParam,
    #[id = "delay_time"]
    delay_time: EnumParam<DelayTime>,
    #[id = "delay_feedback"]
    delay_feedback: FloatParam,
    #[id = "delay_tone"]
    delay_tone: FloatParam,
    #[id = "reverb_mix"]
    reverb_mix: FloatParam,
    #[id = "reverb_size"]
    reverb_size: FloatParam,
    #[id = "reverb_damping"]
    reverb_damping: FloatParam,
    #[id = "volume"]
    volume: FloatParam,
}

impl HardpanParams {
    fn target(&self, target: Target) -> &FloatParam {
        match target {
            Target::Cutoff => &self.cutoff,
            Target::Resonance => &self.resonance,
            Target::EnvAmount => &self.env_amount,
            Target::Drive => &self.drive,
            Target::ChorusMix => &self.chorus_mix,
            Target::DelayMix => &self.delay_mix,
            Target::DelayFeedback => &self.delay_feedback,
            Target::ReverbMix => &self.reverb_mix,
            Target::FilterDecay => &self.filter_env.decay,
            Target::AmpAttack => &self.amp_env.attack,
            Target::AmpRelease => &self.amp_env.release,
            Target::Glide => &self.glide,
        }
    }
}

fn hz(name: &str, default: f32, min: f32, max: f32) -> FloatParam {
    FloatParam::new(name, default, FloatRange::Skewed { min, max, factor: FloatRange::skew_factor(-2.0) })
        // Logarithmic smoothing: a cutoff sweep should move evenly in octaves.
        .with_smoother(SmoothingStyle::Logarithmic(20.0))
        .with_value_to_string(formatters::v2s_f32_hz_then_khz(0))
        .with_string_to_value(formatters::s2v_f32_hz_then_khz())
}

/// The defaults are the phase 3b sequenced bass (SEQ_BASS in the prototype), dry
/// apart from a little reverb.
impl Default for HardpanParams {
    fn default() -> Self {
        Self {
            mode: EnumParam::new("Mode", Mode::Mono),
            oscillators: [
                OscParams::new(WaveChoice::Saw, 0, -4.0, 0.5),
                OscParams::new(WaveChoice::Saw, 0, 4.0, 0.5),
                OscParams::new(WaveChoice::Square, -1, 0.0, 0.3),
            ],
            drive: FloatParam::new("Drive", 2.5, FloatRange::Skewed { min: 0.05, max: 10.0, factor: FloatRange::skew_factor(-1.0) })
                .with_smoother(SmoothingStyle::Logarithmic(30.0))
                .with_value_to_string(formatters::v2s_f32_rounded(2)),
            cutoff: hz("Cutoff", 200.0, 20.0, 20000.0),
            resonance: FloatParam::new("Resonance", 3.5, FloatRange::Skewed { min: 0.5, max: 20.0, factor: FloatRange::skew_factor(-1.5) })
                .with_smoother(SmoothingStyle::Logarithmic(20.0))
                .with_value_to_string(formatters::v2s_f32_rounded(2)),
            slope: EnumParam::new("Slope", SlopeChoice::Db24),
            env_amount: FloatParam::new("Env amount", 3.5, FloatRange::Linear { min: -3.0, max: 6.0 })
                .with_smoother(SmoothingStyle::Linear(20.0))
                .with_unit(" oct")
                .with_value_to_string(formatters::v2s_f32_rounded(2)),
            keytrack: amount("Key tracking", 0.5),
            filter_env: EnvParams::new(0.0, 0.5, 0.0, 0.08),
            amp_env: EnvParams::new(0.0, 0.3, 0.75, 0.06),
            glide: seconds("Glide", 0.06, 1.0),
            accent_gain: FloatParam::new("Accent level", 4.0, FloatRange::Linear { min: 0.0, max: 12.0 })
                .with_unit(" dB")
                .with_value_to_string(formatters::v2s_f32_rounded(1)),
            accent_filter: FloatParam::new("Accent filter", 1.2, FloatRange::Linear { min: 0.0, max: 3.0 })
                .with_unit(" oct")
                .with_value_to_string(formatters::v2s_f32_rounded(2)),
            seq_on: BoolParam::new("Sequencer", false),
            swing: FloatParam::new("Swing", 0.04, FloatRange::Linear { min: 0.0, max: 0.4 })
                .with_value_to_string(formatters::v2s_f32_rounded(2)),
            chorus_mix: amount("Chorus mix", 0.0),
            chorus_rate: FloatParam::new("Chorus rate", 0.5, FloatRange::Skewed { min: 0.05, max: 3.0, factor: FloatRange::skew_factor(-1.0) })
                .with_smoother(SmoothingStyle::Linear(50.0))
                .with_unit(" Hz")
                .with_value_to_string(formatters::v2s_f32_rounded(2)),
            chorus_depth: FloatParam::new("Chorus depth", 1.8, FloatRange::Linear { min: 0.0, max: 3.0 })
                .with_smoother(SmoothingStyle::Linear(50.0))
                .with_unit(" ms")
                .with_value_to_string(formatters::v2s_f32_rounded(2)),
            delay_mix: amount("Delay mix", 0.0),
            delay_time: EnumParam::new("Delay time", DelayTime::DottedEighth),
            delay_feedback: FloatParam::new("Delay feedback", 0.45, FloatRange::Linear { min: 0.0, max: 1.2 })
                .with_smoother(SmoothingStyle::Linear(50.0))
                .with_value_to_string(formatters::v2s_f32_rounded(2)),
            delay_tone: hz("Delay tone", 2500.0, 300.0, 16000.0),
            reverb_mix: amount("Reverb mix", 0.15),
            reverb_size: FloatParam::new("Reverb size", 0.88, FloatRange::Linear { min: 0.5, max: 0.97 })
                .with_smoother(SmoothingStyle::Linear(50.0))
                .with_value_to_string(formatters::v2s_f32_rounded(2)),
            reverb_damping: amount("Reverb damping", 0.5),
            volume: FloatParam::new("Volume", util::db_to_gain(-3.0), FloatRange::gain_range(-48.0, 6.0))
                .with_smoother(SmoothingStyle::Logarithmic(50.0))
                .with_unit(" dB")
                .with_value_to_string(formatters::v2s_f32_gain_to_db(1))
                .with_string_to_value(formatters::s2v_f32_gain_to_db()),
        }
    }
}

struct Hardpan {
    params: Arc<HardpanParams>,
    sample_rate: f64,
    poly: Box<PolySynth<8>>,
    mono: Box<MonoSynth>,
    mode: Mode,
    chorus: JunoChorus<Vec<f32>>,
    delay: TempoDelay<Vec<f32>>,
    reverb: Reverb<Vec<f32>>,
    sequencer: Sequencer<16>,
    sequencing: bool,
    /// Keys held, most recent last; the sequencer plays on the newest.
    keys: [u8; 32],
    keys_len: usize,
    control: u32,
    /// One per `Target`, indexed by it.
    knobs: [Knob; TARGETS],
    /// Knob smoothing per control period, and per sample for the two sends that
    /// are read every sample.
    knob_step: f32,
    knob_step_sample: f32,
    /// Pitch bend in semitones: where the strip is, and the smoothed value in use.
    bend_target: f64,
    bend: f64,
    bend_step: f64,
    /// The mod strip, 0 to 1, likewise.
    mod_target: f64,
    modulation: f64,
    pedal: bool,
    /// Keys let go while the sustain pedal was down, one bit per MIDI note. Their
    /// notes end when the pedal comes up.
    sustained: u128,
}

impl Hardpan {
    fn build(params: Arc<HardpanParams>, sample_rate: f64) -> Self {
        let per_control = |seconds: f64| 1.0 - (-(CONTROL_INTERVAL as f64) / (seconds * sample_rate)).exp();
        let mut plugin = Self {
            params,
            sample_rate,
            poly: Box::new(PolySynth::new(&placeholder_patch(), sample_rate)),
            mono: Box::new(MonoSynth::new(&placeholder_patch(), sample_rate)),
            mode: Mode::Mono,
            chorus: JunoChorus::new(vec![0.0; JunoChorus::<Vec<f32>>::required_len(sample_rate)], sample_rate, 0.5, 3.5, 1.8, 0.0),
            delay: {
                let len = TempoDelay::<Vec<f32>>::required_len(sample_rate);
                TempoDelay::new(vec![0.0; len], vec![0.0; len], sample_rate, 0.4, 0.45, 2500.0)
            },
            reverb: Reverb::new(vec![0.0; Reverb::<Vec<f32>>::required_len(sample_rate)], sample_rate, 0.88, 0.5),
            sequencer: Sequencer::new(BASS_BAR, sample_rate),
            sequencing: false,
            keys: [0; 32],
            keys_len: 0,
            control: 0,
            knobs: [Knob::IDLE; TARGETS],
            knob_step: per_control(KNOB_SMOOTHING_SECONDS) as f32,
            knob_step_sample: (1.0 - (-1.0 / (KNOB_SMOOTHING_SECONDS * sample_rate)).exp()) as f32,
            bend_target: 0.0,
            bend: 0.0,
            bend_step: per_control(BEND_SMOOTHING_SECONDS),
            mod_target: 0.0,
            modulation: 0.0,
            pedal: false,
            sustained: 0,
        };
        plugin.settle_smoothers();
        plugin.mode = plugin.params.mode.value();
        let patch = plugin.patch(1);
        plugin.set_patch(&patch);
        plugin
    }

    fn set_patch(&mut self, patch: &Patch) {
        self.mono.set_patch(patch);
        self.poly.set_patch(&Patch { gain: patch.gain * POLY_GAIN, ..*patch });
    }

    /// Start every smoother at its parameter's current value. The host's wrapper
    /// does this on activation too; doing it here as well means a freshly built
    /// plugin never starts from 0 (silent oscillators, a closed filter).
    fn settle_smoothers(&self) {
        let p = &self.params;
        let mut smoothed: Vec<&FloatParam> = vec![
            &p.drive,
            &p.cutoff,
            &p.resonance,
            &p.env_amount,
            &p.keytrack,
            &p.filter_env.sustain,
            &p.amp_env.sustain,
            &p.chorus_mix,
            &p.chorus_rate,
            &p.chorus_depth,
            &p.delay_mix,
            &p.delay_feedback,
            &p.delay_tone,
            &p.reverb_mix,
            &p.reverb_size,
            &p.reverb_damping,
            &p.volume,
        ];
        for osc in &p.oscillators {
            smoothed.extend([&osc.cents, &osc.level, &osc.width]);
        }
        for param in smoothed {
            param.smoothed.reset(param.value());
        }
    }

    /// Back to silence without allocating, since hosts may call this from the
    /// audio thread. The synths are rebuilt in their existing memory and the
    /// effects' buffers zeroed in place.
    fn clear(&mut self) {
        let patch = self.patch(1);
        *self.poly = PolySynth::new(&patch, self.sample_rate);
        *self.mono = MonoSynth::new(&patch, self.sample_rate);
        self.set_patch(&patch);
        self.chorus.clear();
        self.delay.clear();
        self.reverb.clear();
        self.sequencer = Sequencer::new(BASS_BAR, self.sample_rate);
        self.sequencing = false;
        self.keys_len = 0;
        self.control = 0;
        // The knobs keep their positions: a host stopping and starting playback
        // should not undo what the hands have set. Held notes and the strips do
        // reset, since their releases may never arrive.
        self.bend_target = 0.0;
        self.bend = 0.0;
        self.mod_target = 0.0;
        self.modulation = 0.0;
        self.pedal = false;
        self.sustained = 0;
    }

    /// The current settings as a DSP patch, advancing the smoothers `steps` samples.
    fn patch(&mut self, steps: u32) -> Patch {
        let Self { params, knobs, knob_step, modulation, .. } = self;
        let p: &HardpanParams = params;
        let mut knob = |target: Target, host: f32| knobs[target as usize].value(p.target(target), host, *knob_step);
        let mut filter_env = p.filter_env.values(steps);
        filter_env[1] = knob(Target::FilterDecay, filter_env[1] as f32) as f64;
        let mut amp = p.amp_env.values(steps);
        amp[0] = knob(Target::AmpAttack, amp[0] as f32) as f64;
        amp[3] = knob(Target::AmpRelease, amp[3] as f32) as f64;
        let osc = |o: &OscParams| OscSpec {
            wave: o.wave.value().into(),
            octave: o.octave.value(),
            cents: o.cents.smoothed.next_step(steps) as f64,
            level: o.level.smoothed.next_step(steps),
            width: o.width.smoothed.next_step(steps),
        };
        Patch {
            oscillators: [osc(&p.oscillators[0]), osc(&p.oscillators[1]), osc(&p.oscillators[2])],
            // The mod strip works in octaves on top of the cutoff, wherever the
            // cutoff came from, so the same push opens a dark patch and a bright
            // one by the same musical amount.
            cutoff: knob(Target::Cutoff, p.cutoff.smoothed.next_step(steps)) as f64
                * (MOD_OCTAVES * *modulation).exp2(),
            q: knob(Target::Resonance, p.resonance.smoothed.next_step(steps)) as f64,
            slope: match p.slope.value() {
                SlopeChoice::Db12 => Slope::Db12,
                SlopeChoice::Db24 => Slope::Db24,
            },
            env_amount: knob(Target::EnvAmount, p.env_amount.smoothed.next_step(steps)) as f64,
            keytrack: p.keytrack.smoothed.next_step(steps) as f64,
            filter_env,
            amp,
            gain: VOICE_GAIN,
            // Never 0: switching the drive out entirely would change the delay
            // through the voice and click. tanh at a tiny gain is all but clean.
            drive: knob(Target::Drive, p.drive.smoothed.next_step(steps)).max(0.05) as f64,
            oversample: 2,
            glide: knob(Target::Glide, p.glide.value()) as f64,
            accent_gain: util::db_to_gain(p.accent_gain.value()) as f64,
            accent_env: p.accent_filter.value() as f64,
        }
    }

    fn press(&mut self, key: u8) {
        self.release_key(key);
        if self.keys_len == self.keys.len() {
            self.keys.copy_within(1.., 0);
            self.keys_len -= 1;
        }
        self.keys[self.keys_len] = key;
        self.keys_len += 1;
    }

    fn release_key(&mut self, key: u8) -> bool {
        if let Some(i) = self.keys[..self.keys_len].iter().position(|&k| k == key) {
            self.keys.copy_within(i + 1..self.keys_len, i);
            self.keys_len -= 1;
            true
        } else {
            false
        }
    }

    fn newest_key(&self) -> Option<u8> {
        self.keys[..self.keys_len].last().copied()
    }

    fn synth_on(&mut self, note: i32, accent: bool) {
        match self.mode {
            Mode::Poly => self.poly.note_on(note, accent),
            Mode::Mono => self.mono.note_on(note, accent),
        }
    }

    fn synth_off(&mut self, note: i32) {
        match self.mode {
            Mode::Poly => self.poly.note_off(note),
            Mode::Mono => self.mono.note_off(note),
        }
    }

    fn apply(&mut self, event: SeqEvent) {
        match event {
            SeqEvent::On { note, accent } => self.synth_on(note, accent),
            SeqEvent::Off { note } => self.synth_off(note),
        }
    }

    fn stop_sequencer(&mut self) {
        let mut ends = [0i32; 8];
        let mut count = 0;
        self.sequencer.stop(|e| {
            if let SeqEvent::Off { note } = e {
                if count < ends.len() {
                    ends[count] = note;
                    count += 1;
                }
            }
        });
        for &note in &ends[..count] {
            self.synth_off(note);
        }
    }

    fn release_all(&mut self) {
        match self.mode {
            Mode::Poly => self.poly.release_all(),
            Mode::Mono => self.mono.release_all(),
        }
        self.sustained = 0;
    }

    /// The sustain pedal. Down, it holds every note whose key is let go. With
    /// the sequencer on it latches the pattern instead, so the bass line keeps
    /// running with both hands free for a guitar.
    fn set_pedal(&mut self, down: bool) {
        if down == self.pedal {
            return;
        }
        self.pedal = down;
        if down {
            return;
        }
        let mut waiting = std::mem::take(&mut self.sustained);
        while waiting != 0 {
            let key = waiting.trailing_zeros();
            waiting &= waiting - 1;
            self.synth_off(key as i32);
        }
        if self.sequencing && self.keys_len == 0 && self.sequencer.running() {
            self.stop_sequencer();
        }
    }

    fn control_change(&mut self, cc: u8, value: f32) {
        match cc {
            MOD_CC => self.mod_target = value.clamp(0.0, 1.0) as f64,
            SUSTAIN_CC => self.set_pedal(value >= 0.5),
            ALL_SOUND_OFF_CC | ALL_NOTES_OFF_CC => {
                if self.sequencer.running() {
                    self.stop_sequencer();
                }
                self.release_all();
                self.keys_len = 0;
            }
            _ => {
                if let Some(&(_, target)) = CC_MAP.iter().find(|(number, _)| *number == cc) {
                    self.knobs[target as usize].turn(self.params.target(target), value);
                }
            }
        }
    }

    /// `value` is 0 to 1 with the strip at rest in the middle.
    fn pitch_bend(&mut self, value: f32) {
        // MIDI's 14-bit bend has its centre at 8192 of 16383, which is not
        // exactly half, so work from the raw number to make centre exactly 0.
        let raw = (value as f64 * 16383.0).round() - 8192.0;
        self.bend_target = (raw / 8191.0).clamp(-1.0, 1.0) * BEND_RANGE;
    }

    fn note_on(&mut self, key: u8, velocity: f32) {
        self.press(key);
        // Pressed again, the key holds its note itself; the pedal no longer has to.
        self.sustained &= !(1u128 << (key & 127));
        if self.sequencing {
            // Holding a key plays the pattern on that root; a new key transposes it.
            if self.sequencer.running() {
                self.sequencer.set_root(key as i32);
            } else {
                self.sequencer.start(key as i32);
            }
        } else {
            self.synth_on(key as i32, velocity >= ACCENT_VELOCITY);
        }
    }

    fn note_off(&mut self, key: u8) {
        self.release_key(key);
        if self.sequencing {
            match self.newest_key() {
                Some(root) => self.sequencer.set_root(root as i32),
                None if self.pedal => {} // latched: the pattern runs on until the pedal comes up
                None => self.stop_sequencer(),
            }
        } else if self.pedal {
            self.sustained |= 1u128 << (key & 127);
        } else {
            self.synth_off(key as i32);
        }
    }

    /// One stereo sample: everything `process` does between note events. Kept
    /// separate from `process` so tests can run it without a host.
    fn render_sample(&mut self, bpm: f64) -> (f32, f32) {
        if self.control == 0 {
            self.update_controls(bpm);
            self.control = CONTROL_INTERVAL;
        }
        self.control -= 1;

        if self.sequencer.running() {
            let mut fired = [None; 4];
            let mut count = 0;
            self.sequencer.tick(bpm, |e| {
                if count < fired.len() {
                    fired[count] = Some(e);
                    count += 1;
                }
            });
            for e in fired.into_iter().flatten() {
                self.apply(e);
            }
        }

        // Both synths always run, so one switched away from rings out.
        let voices = self.poly.tick() + self.mono.tick();
        let (chorus_l, chorus_r) = self.chorus.tick(voices);
        let p = &self.params;
        let step = self.knob_step_sample;
        let delay_send =
            self.knobs[Target::DelayMix as usize].value(&p.delay_mix, p.delay_mix.smoothed.next(), step);
        let (echo_l, echo_r) = self.delay.tick(voices * delay_send);
        let reverb_send = voices + (echo_l + echo_r) * 0.5;
        let (verb_l, verb_r) = self.reverb.tick(reverb_send);
        let verb = self.knobs[Target::ReverbMix as usize].value(&p.reverb_mix, p.reverb_mix.smoothed.next(), step)
            * REVERB_RETURN;
        let volume = self.params.volume.smoothed.next();
        let left = (chorus_l + echo_l + verb_l * verb) * volume;
        let right = (chorus_r + echo_r + verb_r * verb) * volume;
        (left, right)
    }

    /// Settings that are checked once per control period.
    fn update_controls(&mut self, bpm: f64) {
        let steps = CONTROL_INTERVAL;
        let mode = self.params.mode.value();
        if mode != self.mode {
            // The old synth releases and rings out on its own; new notes go to the
            // new one. Both keep running, so nothing is cut off.
            if self.sequencing {
                self.stop_sequencer();
            }
            self.release_all();
            self.mode = mode;
            self.keys_len = 0;
        }
        let sequencing = self.params.seq_on.value();
        if sequencing != self.sequencing {
            if self.sequencing {
                self.stop_sequencer();
            } else {
                // Switching on: notes already held by hand are released, and the
                // pattern takes over on the newest key.
                self.release_all();
                if let Some(root) = self.newest_key() {
                    self.sequencer.start(root as i32);
                }
            }
            self.sequencing = sequencing;
        }
        self.sequencer.set_swing(self.params.swing.value() as f64);

        // The strips, smoothed here so their 128 (mod) or coarse (bend) steps do
        // not zipper. Close enough to the target, snap to it: the bend then sits
        // at exactly 0 at rest and the voices stop being retuned.
        self.modulation += (self.mod_target - self.modulation) * self.knob_step as f64;
        if self.bend != self.bend_target {
            self.bend += (self.bend_target - self.bend) * self.bend_step;
            if (self.bend_target - self.bend).abs() < 1e-4 {
                self.bend = self.bend_target;
            }
            self.poly.set_bend(self.bend);
            self.mono.set_bend(self.bend);
        }

        let patch = self.patch(steps);
        self.set_patch(&patch);

        let p = &self.params;
        let step = self.knob_step;
        self.chorus.set(
            p.chorus_rate.smoothed.next_step(steps) as f64,
            3.5,
            p.chorus_depth.smoothed.next_step(steps) as f64,
            self.knobs[Target::ChorusMix as usize].value(&p.chorus_mix, p.chorus_mix.smoothed.next_step(steps), step),
        );
        self.delay.set(
            p.delay_time.value().beats() * 60.0 / bpm,
            self.knobs[Target::DelayFeedback as usize].value(
                &p.delay_feedback,
                p.delay_feedback.smoothed.next_step(steps),
                step,
            ) as f64,
            p.delay_tone.smoothed.next_step(steps) as f64,
        );
        self.reverb.set(
            p.reverb_size.smoothed.next_step(steps) as f64,
            p.reverb_damping.smoothed.next_step(steps) as f64,
        );
    }
}

/// Any valid patch, to build the synths with before the real settings arrive.
fn placeholder_patch() -> Patch {
    Patch {
        oscillators: [OscSpec::OFF; 3],
        cutoff: 1000.0,
        q: 0.7,
        slope: Slope::Db24,
        env_amount: 0.0,
        keytrack: 0.0,
        filter_env: [0.0, 0.1, 1.0, 0.1],
        amp: [0.0, 0.1, 1.0, 0.1],
        gain: VOICE_GAIN,
        drive: 0.05,
        oversample: 2,
        glide: 0.05,
        accent_gain: 1.0,
        accent_env: 0.0,
    }
}

impl Default for Hardpan {
    fn default() -> Self {
        Self::build(Arc::new(HardpanParams::default()), 48000.0)
    }
}

impl Plugin for Hardpan {
    const NAME: &'static str = "HARDPAN";
    const VENDOR: &'static str = "RobotWhiteBread";
    const URL: &'static str = "https://github.com/RobotWhiteBread/HARDPAN";
    const EMAIL: &'static str = "";
    const VERSION: &'static str = env!("CARGO_PKG_VERSION");

    // An instrument: no audio in, stereo out.
    const AUDIO_IO_LAYOUTS: &'static [AudioIOLayout] = &[AudioIOLayout {
        main_input_channels: None,
        main_output_channels: NonZeroU32::new(2),
        aux_input_ports: &[],
        aux_output_ports: &[],
        names: PortNames::const_default(),
    }];

    // MidiCCs rather than Basic, to receive the controller's knobs, strips and pedal.
    const MIDI_INPUT: MidiConfig = MidiConfig::MidiCCs;
    const MIDI_OUTPUT: MidiConfig = MidiConfig::None;
    const SAMPLE_ACCURATE_AUTOMATION: bool = true;

    type Editor = ();
    type SysExMessage = ();
    type BackgroundTask = ();

    fn params(&self) -> Arc<dyn Params> {
        self.params.clone()
    }

    /// Off the audio thread: the one place buffers are allocated, sized for the
    /// host's sample rate.
    fn activate(
        &mut self,
        _audio_io_layout: &AudioIOLayout,
        buffer_config: &BufferConfig,
        _context: &mut impl ActivateContext<Self>,
    ) -> bool {
        let sample_rate = buffer_config.sample_rate as f64;
        if sample_rate != self.sample_rate {
            *self = Self::build(self.params.clone(), sample_rate);
        } else {
            self.clear();
        }
        true
    }

    /// May be called on the audio thread, so it must not allocate: see `clear`.
    fn reset(&mut self) {
        self.clear();
    }

    fn process(
        &mut self,
        buffer: &mut Buffer,
        _aux: &mut AuxiliaryBuffers,
        context: &mut impl ProcessContext<Self>,
    ) -> ProcessStatus {
        let bpm = context.transport().tempo.unwrap_or(FALLBACK_BPM);
        let mut next_event = context.next_event();
        for (sample_id, channel_samples) in buffer.iter_samples().enumerate() {
            while let Some(event) = next_event {
                if event.timing() > sample_id as u32 {
                    break;
                }
                match event {
                    NoteEvent::NoteOn { key, velocity, .. } => {
                        if let Some(k) = key.number() {
                            self.note_on(k, velocity);
                        }
                    }
                    NoteEvent::NoteOff { key, .. } => {
                        if let Some(k) = key.number() {
                            self.note_off(k);
                        }
                    }
                    NoteEvent::MidiCC { cc, value, .. } => self.control_change(cc, value),
                    NoteEvent::MidiPitchBend { value, .. } => self.pitch_bend(value),
                    _ => {}
                }
                next_event = context.next_event();
            }

            let (left, right) = self.render_sample(bpm);
            for (channel, sample) in channel_samples.into_iter().enumerate() {
                *sample = if channel == 0 { left } else { right };
            }
        }
        // The delay and reverb keep ringing after the last note, so keep
        // processing even when no notes are coming in.
        ProcessStatus::KeepAlive
    }
}

impl ClapPlugin for Hardpan {
    const CLAP_ID: &'static str = "com.robotwhitebread.hardpan";
    const CLAP_DESCRIPTION: Option<&'static str> = Some("A synthesizer built from first principles");
    const CLAP_MANUAL_URL: Option<&'static str> = Some(Self::URL);
    const CLAP_SUPPORT_URL: Option<&'static str> = None;
    const CLAP_FEATURES: &'static [ClapFeature] =
        &[ClapFeature::Instrument, ClapFeature::Synthesizer, ClapFeature::Stereo];
}

impl Vst3Plugin for Hardpan {
    const VST3_CLASS_ID: [u8; 16] = *b"HARDPANSynth0001";
    const VST3_SUBCATEGORIES: &'static [Vst3SubCategory] = &[Vst3SubCategory::Instrument, Vst3SubCategory::Synth];
}

nice_export_clap!(Hardpan);
nice_export_vst3!(Hardpan);

#[cfg(test)]
mod tests {
    use super::*;
    use std::alloc::{GlobalAlloc, Layout, System};
    use std::cell::Cell;

    /// Counts allocations made on the current thread, so a test can prove the
    /// audio path makes none.
    struct Counting;
    thread_local! {
        static ALLOCATIONS: Cell<usize> = const { Cell::new(0) };
    }
    unsafe impl GlobalAlloc for Counting {
        unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
            ALLOCATIONS.with(|c| c.set(c.get() + 1));
            unsafe { System.alloc(layout) }
        }
        unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
            unsafe { System.dealloc(ptr, layout) }
        }
    }
    #[global_allocator]
    static GLOBAL: Counting = Counting;

    fn allocations() -> usize {
        ALLOCATIONS.with(|c| c.get())
    }

    fn render(plugin: &mut Hardpan, seconds: f64) -> Vec<(f32, f32)> {
        let n = (seconds * plugin.sample_rate) as usize;
        let mut out = Vec::with_capacity(n);
        for _ in 0..n {
            out.push(plugin.render_sample(120.0));
        }
        out
    }

    fn peak(samples: &[(f32, f32)]) -> f32 {
        samples.iter().fold(0.0f32, |m, (l, r)| m.max(l.abs()).max(r.abs()))
    }

    fn with_params(params: HardpanParams) -> Hardpan {
        Hardpan::build(Arc::new(params), 48000.0)
    }

    fn poly() -> Hardpan {
        with_params(HardpanParams { mode: EnumParam::new("Mode", Mode::Poly), ..HardpanParams::default() })
    }

    fn sequencing() -> Hardpan {
        with_params(HardpanParams { seq_on: BoolParam::new("Sequencer", true), ..HardpanParams::default() })
    }

    #[test]
    fn the_audio_path_never_allocates() {
        // Built first: building is allowed to allocate, playing is not.
        let mut mono = Hardpan::default();
        let mut chords = poly();
        let mut pattern = sequencing();
        let mut out = Vec::with_capacity(48000 * 4);
        let before = allocations();
        // Mono bass with an accent, a poly chord past 8 voices (stealing), then
        // the sequencer: every path through render_sample, note_on and note_off.
        mono.note_on(45, 1.0);
        for _ in 0..24000 {
            out.push(mono.render_sample(120.0));
        }
        mono.note_off(45);
        for k in 0..10 {
            chords.note_on(48 + 3 * k, 0.5);
            for _ in 0..1000 {
                out.push(chords.render_sample(120.0));
            }
        }
        for k in 0..10 {
            chords.note_off(48 + 3 * k);
        }
        for _ in 0..12000 {
            out.push(chords.render_sample(120.0));
        }
        pattern.note_on(40, 0.5);
        for _ in 0..48000 {
            out.push(pattern.render_sample(120.0));
        }
        pattern.note_off(40);
        for _ in 0..24000 {
            out.push(pattern.render_sample(120.0));
        }
        // The controller: every knob and slider, both strips, the pedal, and
        // all-notes-off.
        for (i, (cc, _)) in CC_MAP.iter().enumerate() {
            chords.control_change(*cc, i as f32 / 11.0);
        }
        chords.control_change(MOD_CC, 1.0);
        chords.pitch_bend(1.0);
        chords.control_change(SUSTAIN_CC, 1.0);
        chords.note_on(60, 0.9);
        chords.note_off(60);
        for _ in 0..4800 {
            out.push(chords.render_sample(120.0));
        }
        chords.control_change(SUSTAIN_CC, 0.0);
        pattern.control_change(SUSTAIN_CC, 1.0);
        pattern.note_on(40, 0.5);
        pattern.note_off(40);
        for _ in 0..4800 {
            out.push(pattern.render_sample(120.0));
        }
        pattern.control_change(SUSTAIN_CC, 0.0);
        chords.control_change(ALL_NOTES_OFF_CC, 0.0);
        for _ in 0..1000 {
            out.push(chords.render_sample(120.0));
        }
        // reset(), which a host may call on the audio thread.
        chords.clear();
        for _ in 0..1000 {
            out.push(chords.render_sample(120.0));
        }
        let made = allocations() - before;
        assert_eq!(made, 0, "the audio path allocated {made} times");
        assert!(out.iter().all(|(l, r)| l.is_finite() && r.is_finite()));
    }

    #[test]
    fn a_ten_note_chord_stays_below_full_scale() {
        let mut plugin = poly();
        for k in 0..10 {
            plugin.note_on(45 + 3 * k, 0.5);
        }
        let level = peak(&render(&mut plugin, 1.5));
        assert!(level < 1.0, "peak {level}");
    }

    #[test]
    fn default_patch_plays_at_a_sane_level_and_falls_silent() {
        let mut plugin = Hardpan::default();
        plugin.note_on(45, 0.5);
        let held = render(&mut plugin, 1.0);
        plugin.note_off(45);
        render(&mut plugin, 6.0);
        let tail = render(&mut plugin, 0.5);
        let level = peak(&held);
        assert!(level > 0.05 && level < 1.0, "peak {level}");
        assert!(peak(&tail) < 1e-4, "still sounding: {}", peak(&tail));
    }

    #[test]
    fn a_held_key_runs_the_sequencer_on_that_root() {
        let mut plugin = sequencing();
        plugin.render_sample(120.0); // the first control period switches the sequencer on
        plugin.note_on(45, 0.5);
        let mut notes = Vec::new();
        for _ in 0..48000 {
            plugin.render_sample(120.0);
            if plugin.mono.voice.held && notes.last() != Some(&plugin.mono.voice.note) {
                notes.push(plugin.mono.voice.note);
            }
        }
        // The pattern's first bar: root, root, octave... on A2.
        assert!(notes.starts_with(&[45, 57, 45]), "{notes:?}");
        plugin.note_off(45);
        assert!(!plugin.sequencer.running());
    }

    #[test]
    fn the_pitch_strip_bends_two_semitones_and_returns_to_exactly_zero() {
        let mut plugin = Hardpan::default();
        plugin.note_on(45, 0.5);
        plugin.pitch_bend(1.0);
        render(&mut plugin, 0.1);
        assert!((plugin.mono.voice.bend() - BEND_RANGE).abs() < 1e-3, "{}", plugin.mono.voice.bend());
        plugin.pitch_bend(0.0);
        render(&mut plugin, 0.1);
        assert!((plugin.mono.voice.bend() + BEND_RANGE).abs() < 1e-3, "{}", plugin.mono.voice.bend());
        plugin.pitch_bend(8192.0 / 16383.0); // the strip at rest
        render(&mut plugin, 0.1);
        assert_eq!(plugin.mono.voice.bend(), 0.0);
    }

    #[test]
    fn the_sustain_pedal_holds_notes_until_it_comes_up() {
        let mut plugin = poly();
        plugin.control_change(SUSTAIN_CC, 1.0);
        plugin.note_on(57, 0.5);
        plugin.note_on(60, 0.5);
        render(&mut plugin, 0.1);
        plugin.note_off(57);
        plugin.note_off(60);
        render(&mut plugin, 1.0);
        let held = plugin.poly.voices.iter().filter(|v| v.held).count();
        assert_eq!(held, 2, "the pedal should still be holding both notes");
        // A key pressed again while the pedal is down belongs to the hand again.
        plugin.note_on(60, 0.5);
        plugin.control_change(SUSTAIN_CC, 0.0);
        let held: Vec<i32> = plugin.poly.voices.iter().filter(|v| v.held).map(|v| v.note).collect();
        assert_eq!(held, [60]);
        plugin.note_off(60);
        assert!(plugin.poly.voices.iter().all(|v| !v.held));
    }

    #[test]
    fn the_sustain_pedal_latches_the_sequencer() {
        let mut plugin = sequencing();
        plugin.render_sample(120.0);
        plugin.note_on(45, 0.5);
        plugin.control_change(SUSTAIN_CC, 1.0);
        plugin.note_off(45);
        render(&mut plugin, 1.0);
        assert!(plugin.sequencer.running(), "hands off, pedal down: the pattern keeps going");
        plugin.note_on(48, 0.5); // a new key still transposes it
        plugin.note_off(48);
        assert!(plugin.sequencer.running());
        plugin.control_change(SUSTAIN_CC, 0.0);
        assert!(!plugin.sequencer.running());
    }

    #[test]
    fn a_knob_takes_over_its_parameter_and_glides_there() {
        let mut plugin = Hardpan::default();
        assert!((plugin.patch(1).cutoff - 200.0).abs() < 0.5);
        plugin.control_change(74, 1.0); // knob 1, all the way up
        let first = plugin.patch(CONTROL_INTERVAL).cutoff;
        assert!(first > 200.0 && first < 400.0, "the first step should be small, not a jump: {first}");
        render(&mut plugin, 0.5);
        assert!((plugin.patch(1).cutoff - 20000.0).abs() < 20.0, "{}", plugin.patch(1).cutoff);
        plugin.control_change(74, 0.0);
        render(&mut plugin, 0.5);
        assert!((plugin.patch(1).cutoff - 20.0).abs() < 0.1, "{}", plugin.patch(1).cutoff);
        // Every mapped control reaches the top of its parameter's range.
        for (cc, target) in CC_MAP {
            plugin.control_change(cc, 1.0);
            let knob = plugin.knobs[target as usize];
            assert!(knob.active && knob.target == 1.0, "CC {cc} did not reach {target:?}");
        }
        render(&mut plugin, 0.5);
        let patch = plugin.patch(1);
        assert!((patch.filter_env[1] - 10.0).abs() < 0.01 && (patch.glide - 1.0).abs() < 0.001);
        // An unmapped CC does nothing.
        plugin.control_change(3, 1.0);
    }

    #[test]
    fn a_knob_hands_back_when_the_parameter_itself_changes() {
        let params = HardpanParams::default();
        let mut knob = Knob::IDLE;
        assert_eq!(knob.value(&params.cutoff, 200.0, 1.0), 200.0);
        knob.turn(&params.cutoff, 1.0);
        assert!((knob.value(&params.cutoff, 200.0, 1.0) - 20000.0).abs() < 1.0);
        knob.seen += 0.1; // as if the host had moved the parameter since
        assert_eq!(knob.value(&params.cutoff, 200.0, 1.0), 200.0);
        assert!(!knob.active);
    }

    #[test]
    fn the_mod_strip_opens_the_filter_by_octaves() {
        let mut plugin = Hardpan::default();
        plugin.control_change(MOD_CC, 1.0);
        render(&mut plugin, 0.5);
        let cutoff = plugin.patch(1).cutoff;
        assert!((cutoff / 200.0 - 8.0).abs() < 0.01, "three octaves up from 200 Hz, got {cutoff}");
        plugin.clear(); // a reset lets go of the strips
        assert!((plugin.patch(1).cutoff - 200.0).abs() < 0.5);
    }

    #[test]
    fn all_notes_off_silences_everything() {
        let mut plugin = poly();
        plugin.control_change(SUSTAIN_CC, 1.0);
        for k in [48, 52, 55] {
            plugin.note_on(k, 0.5);
        }
        plugin.note_off(52);
        plugin.control_change(ALL_NOTES_OFF_CC, 0.0);
        assert!(plugin.poly.voices.iter().all(|v| !v.held));
        assert_eq!((plugin.keys_len, plugin.sustained), (0, 0));
    }
}
