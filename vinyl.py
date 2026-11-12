"""VINYL_ARCHIVE phase A1: record one side of a record.

    python vinyl.py record [--artist A] [--album B] [--side A] [--gain NOTE] [--notes TEXT]
                           [--stop-after SECONDS] [--quiet-db DB]
                           [--archive DIR] [--device NAME]

It records stereo from the interface's inputs 1 and 2 to a 24-bit, 48 kHz FLAC
file, shows a peak meter for each channel while it runs, and says so the moment
either channel clips. Anything not given on the command line is asked for.
Press Enter (or Ctrl+C) to stop; the file is finished properly either way.

    <archive>/masters/<Artist>/<Album>/<Artist> - <Album> - Side A.flac
    <archive>/masters/<Artist>/<Album>/<Artist> - <Album> - Side A.json

The archive is "Music\\HARDPAN Vinyl" in your user folder unless HARDPAN_VINYL or
--archive says otherwise. Like the SILT library it is kept out of the
repository: these are commercial recordings.

A master is never overwritten. Recording a side that already exists is refused;
to redo one, move the old files away yourself.

The hookup, levels and routine are in VINYL_ARCHIVE.md, part 1. This records
from the record player through the audio interface, and from nothing else.
"""

import argparse
import json
import os
import queue
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import soundfile as sf

# sounddevice only offers ASIO devices if this is set before it is first imported.
os.environ.setdefault("SD_ENABLE_ASIO", "1")

SAMPLE_RATE = 48000
CHANNELS = 2
BLOCK = 2400  # 50 ms: latency does not matter here, and big blocks are easy on the disk
INTERFACE = "MOTU"

# The interface delivers 24-bit samples in the top of a 32-bit integer. Recording
# them as integers, not floats, means the file holds exactly what the converter
# produced. Full scale is the largest 24-bit value, shifted up 8 bits.
FULL_SCALE = float(2**31)
CLIP_LEVEL = (2**23 - 1) << 8
# Clip reports closer together than this are one event: a clipped passage is
# thousands of clipped samples, and it should not print thousands of lines.
CLIP_MERGE_SECONDS = 1.0

RUNOUT_WINDOW_SECONDS = 0.4
MUSIC_NEEDED_SECONDS = 10.0


def default_archive():
    configured = os.environ.get("HARDPAN_VINYL")
    return Path(configured) if configured else Path.home() / "Music" / "HARDPAN Vinyl"


# File naming --------------------------------------------------------------------

# Windows refuses these in a file name. Each has an obvious stand-in, and using
# them keeps "AC/DC" and "What's Going On?" readable instead of mangled.
SUBSTITUTES = {"/": "-", "\\": "-", ":": " -", "*": "", "?": "", '"': "'", "<": "(", ">": ")", "|": "-"}
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_name(text):
    """`text` as one path component Windows will accept, changed as little as possible."""
    text = "".join(SUBSTITUTES.get(c, c) for c in text if ord(c) >= 32)
    text = re.sub(r"\s+", " ", text).strip(" .")  # Windows drops trailing dots and spaces silently
    if not text or text.upper() in RESERVED:
        text = f"_{text}"
    return text[:80].rstrip(" .")


def side_label(side):
    """'a', 'A', 'side a' and 'Side A' are all side 'A'; '1' stays '1'."""
    side = re.sub(r"^side\s*", "", side.strip(), flags=re.IGNORECASE)
    if not side:
        raise ValueError("a side needs a name, such as A or B")
    return side.upper()


def master_paths(archive, artist, album, side):
    """Where a side's FLAC and its sidecar go: masters/Artist/Album/Artist - Album - Side X."""
    artist_name, album_name = safe_name(artist), safe_name(album)
    stem = f"{artist_name} - {album_name} - Side {safe_name(side_label(side))}"
    folder = Path(archive) / "masters" / artist_name / album_name
    return folder / f"{stem}.flac", folder / f"{stem}.json"


# The recorder -------------------------------------------------------------------


def db(level):
    return 20.0 * np.log10(level) if level > 0 else -np.inf


class SideRecorder:
    """Takes blocks of samples, writes them, and keeps the numbers worth knowing.

    The sound card's callback does not call this. It only puts each block on a
    queue; the main thread takes them off and calls `feed`. Writing a file can
    stall for longer than a block lasts, and a callback that stalls loses audio.
    """

    def __init__(self, path, rate=SAMPLE_RATE, stop_after=None, quiet_db=-40.0):
        self.path = Path(path)
        self.rate = rate
        self.file = sf.SoundFile(str(self.path), "x", samplerate=rate, channels=CHANNELS, format="FLAC",
                                 subtype="PCM_24")
        self.frames = 0
        self.peak = [0.0, 0.0]  # the loudest sample so far in each channel, 1.0 = full scale
        self.block_peak = [0.0, 0.0]
        self.clips = []  # {"seconds", "channel"} for each clipped passage
        self.new_clips = []
        self.dropouts = 0
        # Runout detection: see `_listen_for_runout`.
        self.stop_after = stop_after
        self.quiet = 10.0 ** (quiet_db / 20.0)
        self.window = int(RUNOUT_WINDOW_SECONDS * rate)
        self.window_sum = 0.0
        self.window_count = 0
        self.music_seconds = 0.0
        self.quiet_seconds = 0.0
        self.finished = False

    @property
    def seconds(self):
        return self.frames / self.rate

    def feed(self, block):
        """One block of int32 samples, shape (frames, 2)."""
        self.file.write(block)
        magnitude = np.abs(block.astype(np.int64))
        for channel in range(CHANNELS):
            loudest = int(magnitude[:, channel].max()) if len(block) else 0
            self.block_peak[channel] = loudest / FULL_SCALE
            self.peak[channel] = max(self.peak[channel], self.block_peak[channel])
            if loudest >= CLIP_LEVEL:
                at = self.seconds + int(np.argmax(magnitude[:, channel] >= CLIP_LEVEL)) / self.rate
                recent = [c for c in self.clips if c["channel"] == channel and at - c["seconds"] < CLIP_MERGE_SECONDS]
                if recent:
                    recent[-1]["until"] = round(at, 2)
                else:
                    clip = {"seconds": round(at, 2), "channel": channel}
                    self.clips.append(clip)
                    self.new_clips.append(clip)
        if self.stop_after:
            self._listen_for_runout(block)
        self.frames += len(block)

    def _listen_for_runout(self, block):
        """Notice the end of the side.

        The runout groove is not silent: it has surface noise and a thump once a
        turn. But it is far quieter than music, and it stays that way. So the
        level is measured in 0.4 s windows (RMS, which follows loudness; a peak
        would be fooled by every click), and the side has ended once every
        window has been below the threshold for `stop_after` seconds.

        It only arms after 10 s of music, so the lead-in groove cannot end a
        recording that has not started. A long quiet passage can still fool it,
        which is why it is optional and the threshold adjustable.
        """
        samples = block.astype(np.float64) / FULL_SCALE
        start = 0
        while start < len(samples):
            take = min(self.window - self.window_count, len(samples) - start)
            part = samples[start : start + take]
            self.window_sum += float(np.sum(part * part))
            self.window_count += take
            start += take
            if self.window_count == self.window:
                rms = (self.window_sum / (self.window * CHANNELS)) ** 0.5
                if rms >= self.quiet:
                    self.music_seconds += RUNOUT_WINDOW_SECONDS
                    self.quiet_seconds = 0.0
                elif self.music_seconds >= MUSIC_NEEDED_SECONDS:
                    self.quiet_seconds += RUNOUT_WINDOW_SECONDS
                    if self.quiet_seconds >= self.stop_after:
                        self.finished = True
                self.window_sum = 0.0
                self.window_count = 0

    def close(self):
        self.file.close()


def meter_line(recorder, width=28):
    """One line of text: elapsed time and a bar per channel, scaled from -60 dB to 0."""
    minutes, seconds = divmod(int(recorder.seconds), 60)
    bars = []
    for name, now, peak in zip("LR", recorder.block_peak, recorder.peak):
        filled = int(round(width * min(1.0, max(0.0, (db(now) + 60.0) / 60.0))))
        bars.append(f"{name} {'#' * filled}{'.' * (width - filled)} {db(now):6.1f} dB (max {db(peak):6.1f})")
    return f"{minutes:3d}:{seconds:02d}  " + "  ".join(bars)


def write_sidecar(path, recorder, artist, album, side, gain, notes, device, started):
    side = side_label(side)
    sidecar = {
        "artist": artist,
        "album": album,
        "side": side,
        "file": recorder.path.name,
        "recorded": started.isoformat(timespec="seconds"),
        "gain_note": gain,
        "notes": notes,
        "seconds": round(recorder.seconds, 3),
        "sample_rate": recorder.rate,
        "bit_depth": 24,
        "channels": CHANNELS,
        "peak_db": [round(float(db(p)), 2) if p > 0 else None for p in recorder.peak],
        "clipped": recorder.clips,
        "dropouts": recorder.dropouts,
        "device": device,
        # Everything derived from the archive inherits this (VINYL_ARCHIVE.md).
        "source_type": "commercial",
        "cleared_for_release": False,
    }
    Path(path).write_text(json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return sidecar


# The sound card -----------------------------------------------------------------


def find_input(sd, name):
    """The first device with at least two inputs whose name contains `name`,
    preferring ASIO, which talks to the interface without Windows resampling
    or mixing in between."""
    apis = sd.query_hostapis()
    matches = [
        (index, apis[device["hostapi"]]["name"])
        for index, device in enumerate(sd.query_devices())
        if name.lower() in device["name"].lower() and device["max_input_channels"] >= CHANNELS
    ]
    for wanted in ("ASIO", "WASAPI", ""):
        for index, api in matches:
            if wanted in api:
                return index
    return None


def enter_pressed():
    """True once Enter has been pressed, without waiting for it."""
    try:
        import msvcrt
    except ImportError:  # not Windows: Ctrl+C is the way to stop
        return False
    pressed = False
    while msvcrt.kbhit():
        pressed |= msvcrt.getwch() in "\r\n"
    return pressed


def ask(prompt, given, required=True):
    if given is not None:
        return given
    while True:
        answer = input(prompt).strip()
        if answer or not required:
            return answer
        print("  (needed for the file name)")


def record(args):
    import sounddevice as sd

    index = find_input(sd, args.device or INTERFACE)
    if index is None:
        raise SystemExit(f"No audio input with {args.device or INTERFACE!r} in its name and two channels. "
                         f"Run `rig_check.py devices` to see what is connected.")
    info = sd.query_devices(index)
    device = f"{info['name']} ({sd.query_hostapis()[info['hostapi']]['name']})"

    artist = ask("Artist: ", args.artist)
    album = ask("Album: ", args.album)
    side = ask("Side (A, B, ...): ", args.side)
    gain = ask("Gain note (where the M2's knobs are; Enter to skip): ", args.gain, required=False)
    flac, sidecar = master_paths(args.archive or default_archive(), artist, album, side)
    if flac.exists() or sidecar.exists():
        raise SystemExit(f"{flac} already exists. A master is never overwritten: move it away first to redo it.")
    flac.parent.mkdir(parents=True, exist_ok=True)

    blocks = queue.Queue()
    overflowed = []

    def callback(indata, frames, when, status):
        # The only work done on the audio thread: copy the block and hand it on.
        if status.input_overflow:
            overflowed.append(True)
        blocks.put_nowait(indata.copy())

    recorder = SideRecorder(flac, stop_after=args.stop_after, quiet_db=args.quiet_db)
    started = datetime.now()
    print(f"\nRecording from {device}\n  to {flac}")
    print("Lower the needle. Press Enter to stop" +
          (f", or it stops after {args.stop_after:g} s of runout groove.\n" if args.stop_after else ".\n"))
    interrupted = False
    try:
        with sd.InputStream(device=index, samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int32",
                            blocksize=BLOCK, callback=callback):
            last_drawn = 0.0
            while not recorder.finished and not enter_pressed():
                try:
                    recorder.feed(blocks.get(timeout=0.2))
                except queue.Empty:
                    continue
                while overflowed:
                    overflowed.pop()
                    recorder.dropouts += 1
                    print(f"\r{' ' * 110}\r  DROPOUT at {recorder.seconds:.1f} s: the computer fell behind and "
                          f"audio was lost. Close other programs and redo this side.")
                for clip in recorder.new_clips:
                    print(f"\r{' ' * 110}\r  CLIPPED at {int(clip['seconds'] // 60)}:{clip['seconds'] % 60:04.1f} "
                          f"({'LR'[clip['channel']]}). Lower the gain and start this side again.")
                recorder.new_clips.clear()
                if time.monotonic() - last_drawn > 0.1:
                    print("\r" + meter_line(recorder), end="", flush=True)
                    last_drawn = time.monotonic()
    except KeyboardInterrupt:
        interrupted = True
    finally:
        while not blocks.empty():  # what arrived between the last read and the stream closing
            recorder.feed(blocks.get_nowait())
        recorder.close()
    print()

    if recorder.frames == 0:
        flac.unlink(missing_ok=True)
        raise SystemExit("Nothing was recorded.")
    notes = args.notes
    if notes is None and not interrupted:
        notes = input("Notes (skips, warps, a passage worth sampling; Enter to skip): ").strip()
    result = write_sidecar(sidecar, recorder, artist, album, side, gain or "", notes or "", device, started)

    minutes, seconds = divmod(result["seconds"], 60)
    left, right = (f"{p:.1f} dB" if p is not None else "silent" for p in result["peak_db"])
    print(f"Saved {flac.name}: {int(minutes)}:{seconds:04.1f}, peaks L {left}, R {right}")
    if recorder.clips:
        print(f"  It clipped {len(recorder.clips)} time(s). Clipping cannot be repaired: lower the gain and redo it.")
    if recorder.dropouts:
        print(f"  {recorder.dropouts} dropout(s): audio is missing from this recording. Redo it.")
    if None in result["peak_db"] or min(p for p in result["peak_db"] if p is not None) < -40:
        print("  One channel is silent or very quiet. Check both RCA plugs and both gain knobs.")
    elif abs(result["peak_db"][0] - result["peak_db"][1]) > 6:
        print("  The channels differ by more than 6 dB. Check the two gain knobs sit at the same position.")
    elif max(result["peak_db"]) < -18 and not recorder.clips:
        print("  Quiet: the loudest passage should peak near -6 dB. Raise both gain knobs next time.")
    print("Spot check: listen to 20 seconds near the start, middle and end.")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(prog="vinyl.py", description="VINYL_ARCHIVE: record the collection")
    commands = parser.add_subparsers(dest="command", required=True)
    rec = commands.add_parser("record", help="record one side to a 24-bit 48 kHz FLAC master")
    rec.add_argument("--artist")
    rec.add_argument("--album")
    rec.add_argument("--side")
    rec.add_argument("--gain", help="a note of where the interface's gain knobs are")
    rec.add_argument("--notes", help="free text: skips, warps, a passage worth sampling")
    rec.add_argument("--stop-after", type=float, metavar="SECONDS",
                     help="stop by itself after this long in the runout groove (off unless given; try 20)")
    rec.add_argument("--quiet-db", type=float, default=-40.0,
                     help="the level below which it counts as runout groove (default -40 dB)")
    rec.add_argument("--archive", type=Path, help="archive folder (default: Music\\HARDPAN Vinyl)")
    rec.add_argument("--device", help="part of the audio device's name (default: the MOTU)")
    args = parser.parse_args(argv)
    if args.command == "record":
        record(args)


if __name__ == "__main__":
    main(sys.argv[1:])
