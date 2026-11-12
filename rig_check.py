"""Checks for the hardware rig: is everything connected, what does the keyboard
send, and how long does sound take to go out of the interface and back in.

    python rig_check.py devices              what Windows can see, and what is missing
    python rig_check.py midi                 print every MIDI message as it arrives
    python rig_check.py latency [--device NAME] [--block FRAMES] [--rate HZ]

`latency` needs one cable from the interface's output 1 to its input 1, with
nothing in between. It plays a short burst of noise and finds it in the
recording. Turn headphones and speakers down first: on a MOTU M2 they carry the
same signal as output 1.

The rig this is written for: a MOTU M2 interface, an Arturia MiniLab 3 keyboard
and an Arturia MicroFreak synth. Nothing here is specific to them except the
names it looks for and the table of what the MiniLab's controls do in HARDPAN.
"""

import argparse
import os
import sys
import time

import numpy as np

# sounddevice only offers ASIO devices if this is set before it is first imported.
os.environ.setdefault("SD_ENABLE_ASIO", "1")

# The devices the rig expects, by a word that appears in their names.
INTERFACE = "MOTU"
KEYBOARD = "MiniLab"
SYNTH = "MicroFreak"

# What HARDPAN's plugin does with each controller message (CC_MAP in
# plugin/src/lib.rs; keep the two in step). The numbers are what a MiniLab 3
# sends from a factory User program.
CONTROLS = {
    74: "knob 1: cutoff",
    71: "knob 2: resonance",
    76: "knob 3: filter envelope amount",
    77: "knob 4: drive",
    93: "knob 5: chorus mix",
    18: "knob 6: delay mix",
    19: "knob 7: delay feedback",
    16: "knob 8: reverb mix",
    82: "slider 1: filter decay",
    83: "slider 2: amp attack",
    85: "slider 3: amp release",
    17: "slider 4: glide",
    1: "mod strip: opens the filter",
    64: "sustain pedal: holds notes, or latches the sequencer",
    120: "all sound off",
    123: "all notes off",
}

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(note):
    return f"{NOTE_NAMES[note % 12]}{note // 12 - 1}"


def describe(msg):
    """One line for one mido message, saying what HARDPAN will do with it."""
    channel = f"ch {msg.channel + 1:<2}" if hasattr(msg, "channel") else "     "
    if msg.type == "note_on" and msg.velocity > 0:
        accent = "  (accented: velocity 102 or more)" if msg.velocity >= 102 else ""
        return f"{channel}  note on   {note_name(msg.note):<4} velocity {msg.velocity}{accent}"
    if msg.type in ("note_on", "note_off"):
        return f"{channel}  note off  {note_name(msg.note)}"
    if msg.type == "control_change":
        meaning = CONTROLS.get(msg.control, "not used by HARDPAN")
        return f"{channel}  CC {msg.control:<3} = {msg.value:<3}  {meaning}"
    if msg.type == "pitchwheel":
        return f"{channel}  pitch bend {msg.pitch:+6d}  {msg.pitch / 8192 * 2:+.2f} semitones in HARDPAN"
    return f"{channel}  {msg}"


# Devices ----------------------------------------------------------------------


def check_devices():
    import mido
    import sounddevice as sd

    apis = sd.query_hostapis()
    print("Audio devices")
    for index, device in enumerate(sd.query_devices()):
        api = apis[device["hostapi"]]["name"]
        print(f"  [{index:2}] {device['name']}  ({api}, {device['max_input_channels']} in, "
              f"{device['max_output_channels']} out, {device['default_samplerate']:.0f} Hz)")
    inputs, outputs = mido.get_input_names(), mido.get_output_names()
    print("\nMIDI inputs")
    for name in inputs or ["(none)"]:
        print(f"  {name}")
    print("\nMIDI outputs")
    for name in outputs or ["(none)"]:
        print(f"  {name}")

    print("\nThe rig")
    asio = find_device(sd, INTERFACE, "ASIO")
    any_api = find_device(sd, INTERFACE, None)
    checks = [
        (asio is not None, f"{INTERFACE} interface over ASIO",
         "install the MOTU M-Series driver, then replug the interface" if any_api is None
         else "Windows sees the interface but not its ASIO driver: install the MOTU M-Series driver"),
        (any(KEYBOARD.lower() in n.lower() for n in inputs), f"{KEYBOARD} keyboard as a MIDI input",
         "plug it into the powered hub; it needs no driver"),
        (any(SYNTH.lower() in n.lower() for n in inputs + outputs), f"{SYNTH} over USB MIDI",
         "optional: only needed to send it MIDI from the computer or load samples"),
    ]
    for found, what, fix in checks:
        print(f"  {'found  ' if found else 'MISSING'}  {what}" + ("" if found else f"  ->  {fix}"))
    return all(found for found, _, _ in checks[:2])


def find_device(sd, name, api):
    """Index of the first duplex device whose name contains `name`, on the host
    API whose name contains `api` (any API if None)."""
    apis = sd.query_hostapis()
    for index, device in enumerate(sd.query_devices()):
        if name.lower() not in device["name"].lower():
            continue
        if api and api.lower() not in apis[device["hostapi"]]["name"].lower():
            continue
        if device["max_input_channels"] > 0 and device["max_output_channels"] > 0:
            return index
    return None


# MIDI monitor -----------------------------------------------------------------


def monitor_midi():
    import mido

    names = mido.get_input_names()
    if not names:
        print("No MIDI inputs. Is the keyboard plugged in?")
        return
    started = time.monotonic()

    def printer(port_name):
        def on_message(msg):
            if msg.type in ("clock", "active_sensing"):  # sent constantly; they would bury everything else
                return
            print(f"{time.monotonic() - started:8.3f}  {port_name[:24]:<24}  {describe(msg)}", flush=True)
        return on_message

    ports = []
    for name in names:
        try:
            ports.append(mido.open_input(name, callback=printer(name)))
        except Exception as error:  # rtmidi raises its own error types; a DAW may hold the port
            print(f"Could not open {name!r} (is REAPER using it?): {error}")
    print("Listening on:", ", ".join(port.name for port in ports) or "(nothing)")
    print("Play keys, turn every knob, move the strips. Ctrl+C to stop.")
    print("If the knobs say 'not used by HARDPAN', put the MiniLab on a User program: Shift + Pad 3.\n")
    try:
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        for port in ports:
            port.close()


# Round-trip latency -----------------------------------------------------------


def test_signal(rate, seconds=0.1, level=0.125, seed=7):
    """A burst of white noise with silence either side.

    Noise rather than a click or a tone because its autocorrelation is a single
    sharp spike: slide it against a copy of itself and it only matches at one
    offset. A tone matches again every cycle, and a click is one sample that any
    noise on the line can outvote.
    """
    rng = np.random.default_rng(seed)
    burst = rng.uniform(-1.0, 1.0, int(seconds * rate)) * level
    fade = int(0.002 * rate)  # 2 ms in and out, so the burst is not also two clicks
    ramp = 0.5 - 0.5 * np.cos(np.pi * np.arange(fade) / fade)
    burst[:fade] *= ramp
    burst[-fade:] *= ramp[::-1]
    silence = np.zeros(int(0.25 * rate))
    return np.concatenate([silence, burst, np.zeros(int(0.65 * rate))]).astype(np.float32)


def measure_delay(sent, received):
    """How many samples later `received` holds `sent`, and how sure that is.

    Cross-correlation: for every possible delay, multiply the two signals
    together and add up. The sum is largest at the delay where they line up.
    Computing it directly costs N squared multiplies; in the frequency domain it
    is one multiply per bin (the correlation theorem), so three FFTs do it.

    The confidence is the height of that peak over the typical size of the
    correlation everywhere else. A real loopback gives hundreds; a recording of
    nothing but noise gives single digits.
    """
    n = len(sent) + len(received)
    size = 1 << (n - 1).bit_length()  # zero-padded, so a delay cannot wrap around to look like an advance
    spectrum = np.fft.rfft(received.astype(np.float64), size) * np.conj(np.fft.rfft(sent.astype(np.float64), size))
    correlation = np.fft.irfft(spectrum, size)[: len(received)]  # delays of 0 and up only
    delay = int(np.argmax(np.abs(correlation)))
    floor = float(np.median(np.abs(correlation))) + 1e-20
    return delay, float(np.abs(correlation[delay])) / floor


def measure_latency(device=None, blocksize=256, rate=48000, repeats=3):
    import sounddevice as sd

    if device is None:
        index = find_device(sd, INTERFACE, "ASIO")
        if index is None:
            index = find_device(sd, INTERFACE, None)
        if index is None:
            raise SystemExit(f"No {INTERFACE} interface found. Run `rig_check.py devices`, or pass --device NAME.")
    else:
        index = find_device(sd, device, None)
        if index is None:
            raise SystemExit(f"No duplex audio device with {device!r} in its name.")
    info = sd.query_devices(index)
    api = sd.query_hostapis()[info["hostapi"]]["name"]
    print(f"Device: {info['name']} ({api}), {rate} Hz, block {blocksize} frames "
          f"({blocksize / rate * 1000:.1f} ms)")
    print("Cable: output 1 -> input 1. Playing a short burst of noise, quietly...")

    sent = test_signal(rate)
    delays = []
    for attempt in range(repeats):
        recorded = sd.playrec(sent[:, None], samplerate=rate, channels=1, device=index,
                              blocksize=blocksize, dtype="float32")
        sd.wait()
        delay, confidence = measure_delay(sent, recorded[:, 0])
        level = float(np.max(np.abs(recorded)))
        if confidence < 30.0:
            print(f"  run {attempt + 1}: the burst did not come back (input peak {level:.4f}). Check the "
                  f"cable goes from output 1 to input 1, and turn input 1's gain up.")
            continue
        clipped = "  INPUT CLIPPED: turn the gain down" if level >= 0.999 else ""
        print(f"  run {attempt + 1}: {delay} samples = {delay / rate * 1000:.2f} ms  (input peak {level:.3f}){clipped}")
        delays.append(delay)
    if not delays:
        return None
    best = int(np.median(delays))
    print(f"\nRound trip: {best} samples = {best / rate * 1000:.2f} ms at a {blocksize}-frame block.")
    print(f"Of that, {2 * blocksize} samples are the two buffers (one in, one out); the other "
          f"{best - 2 * blocksize} are the driver and converters.")
    if max(delays) - min(delays) > 2:
        print(f"The runs differ by {max(delays) - min(delays)} samples, so do not trust this for aligning "
              f"captures; a steady driver repeats to the sample.")
    print("Under 10 ms feels immediate on guitar. To align a pedal capture, shift the recording "
          f"earlier by {best} samples.")
    return best


def main(argv=None):
    parser = argparse.ArgumentParser(prog="rig_check.py", description="Checks for the HARDPAN hardware rig")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("devices", help="list audio and MIDI devices and say what is missing")
    commands.add_parser("midi", help="print incoming MIDI messages")
    latency = commands.add_parser("latency", help="measure round-trip latency through a loopback cable")
    latency.add_argument("--device", help="part of the audio device's name (default: the MOTU over ASIO)")
    latency.add_argument("--block", type=int, default=256, help="block size in frames")
    latency.add_argument("--rate", type=int, default=48000)
    args = parser.parse_args(argv)
    if args.command == "devices":
        check_devices()
    elif args.command == "midi":
        monitor_midi()
    elif args.command == "latency":
        measure_latency(args.device, args.block, args.rate)


if __name__ == "__main__":
    main(sys.argv[1:])
