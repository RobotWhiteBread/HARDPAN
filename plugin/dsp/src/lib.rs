//! HARDPAN's signal processing, ported from the Python prototype (`hardpan_proto.py`).
//!
//! The reasoning behind each piece of math lives in the prototype's comments; the
//! comments here cover what is different about doing it in Rust on an audio thread.
//!
//! This crate is `no_std` and never allocates, so the same code can run inside the
//! plugin and, later, on a Daisy Seed pedal. Anything that needs memory (delay
//! lines, the reverb) takes its buffer from the caller, who decides where it lives:
//! a `Vec` allocated before playback in the plugin, a static array on the pedal.
//!
//! The arithmetic follows the prototype operation for operation, in `f32` where the
//! prototype used float32 and in `f64` where it computed coefficients in double, so
//! the tests can check this port against samples rendered by the prototype itself.
#![cfg_attr(not(test), no_std)]

pub mod drive;
pub mod effects;
pub mod envelope;
pub mod filter;
pub mod oscillator;
pub mod sequencer;
pub mod voice;
