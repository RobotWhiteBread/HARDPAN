//! A minimal CLAP host that loads the packaged plugin the way a DAW would:
//! entry point, factory, instance, activate, then audio blocks with MIDI notes.
//! It checks the plugin instantiates, makes sound for a held note, stays below
//! full scale, and falls silent after release.
//!
//! It tests the shipped file, so build it first, then run the test:
//!
//!   .\bundle.ps1
//!   cargo test --release --test clap_host -- --ignored --nocapture
//!
//! Setting HARDPAN_CLAP to another build (target\debug\hardpan.dll, say) tests
//! that instead. A debug build is worth it: there nice-plug's
//! `assert_process_allocs` makes any allocation in the audio callback fail.
#![cfg(windows)]

use clap_sys::audio_buffer::clap_audio_buffer;
use clap_sys::entry::clap_plugin_entry;
use clap_sys::events::{
    CLAP_CORE_EVENT_SPACE_ID, CLAP_EVENT_NOTE_OFF, CLAP_EVENT_NOTE_ON, clap_event_header, clap_event_note,
    clap_input_events, clap_output_events,
};
use clap_sys::factory::plugin_factory::{CLAP_PLUGIN_FACTORY_ID, clap_plugin_factory};
use clap_sys::host::clap_host;
use clap_sys::plugin::clap_plugin;
use clap_sys::process::{CLAP_PROCESS_ERROR, clap_process};
use clap_sys::version::CLAP_VERSION;
use std::ffi::{CStr, c_char, c_void};
use std::path::PathBuf;

#[link(name = "kernel32")]
unsafe extern "system" {
    fn LoadLibraryW(name: *const u16) -> *mut c_void;
    fn GetProcAddress(module: *mut c_void, name: *const u8) -> *mut c_void;
}

unsafe extern "C" fn no_extension(_: *const clap_host, _: *const c_char) -> *const c_void {
    std::ptr::null()
}
unsafe extern "C" fn ignore(_: *const clap_host) {}

unsafe extern "C" fn events_size(list: *const clap_input_events) -> u32 {
    unsafe { (*((*list).ctx as *const Vec<clap_event_note>)).len() as u32 }
}
unsafe extern "C" fn events_get(list: *const clap_input_events, index: u32) -> *const clap_event_header {
    unsafe {
        let events: &Vec<clap_event_note> = &*((*list).ctx as *const Vec<clap_event_note>);
        &events[index as usize].header
    }
}
unsafe extern "C" fn discard(_: *const clap_output_events, _: *const clap_event_header) -> bool {
    true
}

fn note(kind: u16, key: i16, velocity: f64) -> clap_event_note {
    clap_event_note {
        header: clap_event_header {
            size: std::mem::size_of::<clap_event_note>() as u32,
            time: 0,
            space_id: CLAP_CORE_EVENT_SPACE_ID,
            type_: kind,
            flags: 0,
        },
        note_id: -1,
        port_index: 0,
        channel: 0,
        key,
        velocity,
    }
}

#[test]
#[ignore = "needs the packaged plugin: run bundle.ps1 first"]
fn the_packaged_clap_plugin_loads_plays_and_falls_silent() {
    let path = std::env::var_os("HARDPAN_CLAP").map(PathBuf::from).unwrap_or_else(|| {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("target").join("bundled").join("HARDPAN.clap")
    });
    assert!(path.exists(), "{} not found: run bundle.ps1 first", path.display());
    let wide: Vec<u16> = path.as_os_str().to_string_lossy().encode_utf16().chain([0]).collect();

    unsafe {
        let module = LoadLibraryW(wide.as_ptr());
        assert!(!module.is_null(), "could not load the plugin");
        let entry = GetProcAddress(module, c"clap_entry".as_ptr() as *const u8) as *const clap_plugin_entry;
        assert!(!entry.is_null(), "no clap_entry");
        let entry = &*entry;
        let path_c = std::ffi::CString::new(path.to_string_lossy().as_bytes()).unwrap();
        assert!((entry.init.unwrap())(path_c.as_ptr()), "entry init failed");

        let factory = (entry.get_factory.unwrap())(CLAP_PLUGIN_FACTORY_ID.as_ptr()) as *const clap_plugin_factory;
        assert!(!factory.is_null(), "no plugin factory");
        assert_eq!(((*factory).get_plugin_count.unwrap())(factory), 1);
        let descriptor = ((*factory).get_plugin_descriptor.unwrap())(factory, 0);
        let id = CStr::from_ptr((*descriptor).id);
        let name = CStr::from_ptr((*descriptor).name);
        println!("found {:?} ({:?})", name, id);
        assert_eq!(id.to_str().unwrap(), "com.robotwhitebread.hardpan");

        let host = clap_host {
            clap_version: CLAP_VERSION,
            host_data: std::ptr::null_mut(),
            name: c"hardpan smoke test".as_ptr(),
            vendor: c"".as_ptr(),
            url: c"".as_ptr(),
            version: c"0".as_ptr(),
            get_extension: Some(no_extension),
            request_restart: Some(ignore),
            request_process: Some(ignore),
            request_callback: Some(ignore),
        };
        let plugin: *const clap_plugin = ((*factory).create_plugin.unwrap())(factory, &host, (*descriptor).id);
        assert!(!plugin.is_null(), "create_plugin failed");
        let p = &*plugin;
        assert!((p.init.unwrap())(plugin), "plugin init failed");
        const BLOCK: u32 = 512;
        assert!((p.activate.unwrap())(plugin, 48000.0, 1, BLOCK), "activate failed");
        assert!((p.start_processing.unwrap())(plugin), "start_processing failed");

        let mut left = vec![0.0f32; BLOCK as usize];
        let mut right = vec![0.0f32; BLOCK as usize];
        let out_events = clap_output_events { ctx: std::ptr::null_mut(), try_push: Some(discard) };
        let blocks = (7.0 * 48000.0 / BLOCK as f64) as usize;
        let release_at = (0.5 * 48000.0 / BLOCK as f64) as usize;
        let mut peak_held = 0.0f32;
        let mut peak_end = 0.0f32;
        for block in 0..blocks {
            let mut events: Vec<clap_event_note> = Vec::new();
            if block == 0 {
                events.push(note(CLAP_EVENT_NOTE_ON, 45, 0.9));
            }
            if block == release_at {
                events.push(note(CLAP_EVENT_NOTE_OFF, 45, 0.0));
            }
            let in_events = clap_input_events {
                ctx: &mut events as *mut Vec<clap_event_note> as *mut c_void,
                size: Some(events_size),
                get: Some(events_get),
            };
            let mut channels = [left.as_mut_ptr(), right.as_mut_ptr()];
            let mut output = clap_audio_buffer {
                data32: channels.as_mut_ptr(),
                data64: std::ptr::null_mut(),
                channel_count: 2,
                latency: 0,
                constant_mask: 0,
            };
            let process = clap_process {
                steady_time: (block as u32 * BLOCK) as i64,
                frames_count: BLOCK,
                transport: std::ptr::null(),
                audio_inputs: std::ptr::null(),
                audio_outputs: &mut output,
                audio_inputs_count: 0,
                audio_outputs_count: 1,
                in_events: &in_events,
                out_events: &out_events,
            };
            let status = (p.process.unwrap())(plugin, &process);
            assert_ne!(status, CLAP_PROCESS_ERROR, "process failed in block {block}");
            let block_peak = left.iter().chain(&right).fold(0.0f32, |m, v| {
                assert!(v.is_finite(), "non-finite output in block {block}");
                m.max(v.abs())
            });
            if block < release_at {
                peak_held = peak_held.max(block_peak);
            }
            if block >= blocks - 10 {
                peak_end = peak_end.max(block_peak);
            }
        }
        println!("peak while held {peak_held:.3}, in the last 0.1 s {peak_end:.2e}");
        assert!(peak_held > 0.05 && peak_held < 1.0, "peak while held {peak_held}");
        assert!(peak_end < 1e-3, "still sounding after 6.5 s: {peak_end}");

        (p.stop_processing.unwrap())(plugin);
        (p.deactivate.unwrap())(plugin);
        (p.destroy.unwrap())(plugin);
        (entry.deinit.unwrap())();
    }
}
