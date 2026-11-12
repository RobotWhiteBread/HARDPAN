//! A real-time 16th-note step sequencer, the plugin's version of the prototype's
//! `Step` and `sequence()`.
//!
//! The prototype turned a whole sequence into notes in advance. A plugin cannot:
//! the tempo can change at any moment and the pattern runs only while a key is
//! held. So this one counts in steps rather than seconds, advancing by
//! bpm / 60 * 4 / sample_rate steps per sample, and fires each event on the first
//! sample at or past its time. Tempo changes then take effect smoothly mid-pattern.

#[derive(Clone, Copy, Debug)]
pub struct Step {
    /// Semitones above the root; `None` is a rest.
    pub interval: Option<i32>,
    /// Fraction of a step the note is held. Above 1 it runs into the next step,
    /// which on a mono patch makes the next note glide from it.
    pub gate: f64,
    pub accent: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SeqEvent {
    On { note: i32, accent: bool },
    Off { note: i32 },
}

const MAX_PENDING: usize = 8;

pub struct Sequencer<const N: usize> {
    steps: [Step; N],
    swing: f64,
    sample_rate: f64,
    running: bool,
    root: i32,
    position: f64,
    next_step: u64,
    pending: [(f64, i32); MAX_PENDING],
    pending_len: usize,
}

impl<const N: usize> Sequencer<N> {
    pub fn new(steps: [Step; N], sample_rate: f64) -> Self {
        Self {
            steps,
            swing: 0.0,
            sample_rate,
            running: false,
            root: 0,
            position: 0.0,
            next_step: 0,
            pending: [(0.0, 0); MAX_PENDING],
            pending_len: 0,
        }
    }

    pub fn running(&self) -> bool {
        self.running
    }

    /// Pushes every second 16th late by this fraction of a step (0 straight,
    /// about 0.33 a full triplet shuffle).
    pub fn set_swing(&mut self, swing: f64) {
        self.swing = swing.clamp(0.0, 0.5);
    }

    /// Start the pattern from its first step, on `root`.
    pub fn start(&mut self, root: i32) {
        self.running = true;
        self.root = root;
        self.position = 0.0;
        self.next_step = 0;
    }

    /// Change key without restarting the pattern; the next step uses the new root.
    pub fn set_root(&mut self, root: i32) {
        self.root = root;
    }

    /// Stop, ending any notes still sounding.
    pub fn stop(&mut self, mut emit: impl FnMut(SeqEvent)) {
        for &(_, note) in &self.pending[..self.pending_len] {
            emit(SeqEvent::Off { note });
        }
        self.pending_len = 0;
        self.running = false;
    }

    fn start_of(&self, step: u64) -> f64 {
        step as f64 + if step % 2 == 1 { self.swing } else { 0.0 }
    }

    /// Advance one sample at `bpm`, emitting note-offs before note-ons, as the
    /// prototype's event ordering does.
    pub fn tick(&mut self, bpm: f64, mut emit: impl FnMut(SeqEvent)) {
        if !self.running {
            return;
        }
        let next = self.position + bpm / 60.0 * 4.0 / self.sample_rate;
        let mut i = 0;
        while i < self.pending_len {
            if self.pending[i].0 <= self.position {
                emit(SeqEvent::Off { note: self.pending[i].1 });
                self.pending.copy_within(i + 1..self.pending_len, i);
                self.pending_len -= 1;
            } else {
                i += 1;
            }
        }
        while self.start_of(self.next_step) <= self.position {
            let at = self.start_of(self.next_step);
            let step = self.steps[(self.next_step % N as u64) as usize];
            if let Some(interval) = step.interval {
                let note = self.root + interval;
                emit(SeqEvent::On { note, accent: step.accent });
                if self.pending_len < MAX_PENDING {
                    self.pending[self.pending_len] = (at + step.gate, note);
                    self.pending_len += 1;
                }
            }
            self.next_step += 1;
        }
        self.position = next;
    }
}

/// The prototype's BASS_BAR: accents every third 16th, octave jumps between them,
/// and the last three steps tied so they glide.
pub const BASS_BAR: [Step; 16] = {
    const fn s(interval: i32, gate: f64, accent: bool) -> Step {
        Step { interval: Some(interval), gate, accent }
    }
    [
        s(0, 0.5, true), s(0, 0.5, false), s(12, 0.5, false),
        s(0, 0.5, true), s(0, 0.5, false), s(12, 0.5, false),
        s(0, 0.5, true), s(0, 0.5, false), s(12, 0.5, false),
        s(0, 0.5, true), s(0, 0.5, false), s(12, 0.5, false),
        s(0, 0.5, true), s(12, 1.1, false), s(7, 1.1, false), s(12, 0.5, false),
    ]
};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn events_land_on_the_16th_grid_with_swing_and_ties() {
        // 120 BPM at 48 kHz: a 16th is exactly 6000 samples.
        let mut seq = Sequencer::new(BASS_BAR, 48000.0);
        seq.set_swing(0.1);
        seq.start(45);
        let mut events = Vec::new();
        for n in 0..(16 * 6000) {
            seq.tick(120.0, |e| events.push((n, e)));
        }
        // The position is accumulated sample by sample, so an event can land one
        // sample either side of its exact time.
        let near = |a: usize, b: usize| a.abs_diff(b) <= 1;
        let ons: Vec<_> = events.iter().filter(|(_, e)| matches!(e, SeqEvent::On { .. })).collect();
        assert_eq!(ons.len(), 16);
        assert_eq!(ons[0], &(0, SeqEvent::On { note: 45, accent: true }));
        assert!(near(ons[1].0, 6000 + 600), "{}", ons[1].0); // the off-beat 16th, 10% late
        assert!(near(ons[2].0, 12000) && ons[2].1 == SeqEvent::On { note: 57, accent: false });
        // Step 13 (gate 1.1) is still sounding when step 14 starts: a tie.
        let on14 = ons[14].0;
        let off13 = events.iter().find(|(n, e)| *e == SeqEvent::Off { note: 57 } && *n > ons[13].0).unwrap().0;
        assert!(off13 > on14, "tie: off {off13} after next on {on14}");
        // Detached notes end at half a step.
        let off0 = events.iter().find(|(_, e)| *e == SeqEvent::Off { note: 45 }).unwrap().0;
        assert!(near(off0, 3000), "{off0}");
    }

    #[test]
    fn stop_ends_held_notes() {
        let mut seq = Sequencer::new(BASS_BAR, 48000.0);
        seq.start(40);
        seq.tick(120.0, |_| {});
        let mut offs = Vec::new();
        seq.stop(|e| offs.push(e));
        assert_eq!(offs, vec![SeqEvent::Off { note: 40 }]);
        assert!(!seq.running());
    }
}
