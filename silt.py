"""SILT: outside audio for HARDPAN. Phase S1 (ingest and library) and the
hardware export from phase S6.

Silt is fine sediment carried in from somewhere else and laid down on top of
the hardpan. Here that is audio from outside the synth (your own recordings,
AI-generated files, tracks you own copies of, sample packs) brought into one
library that HARDPAN can play.

    python silt.py ingest PATH [PATH ...] --source own|ai|commercial|pack [options]
    python silt.py list [--source TYPE] [--tag TAG]
    python silt.py search TEXT
    python silt.py show ID
    python silt.py tag ID [--add TAG ...] [--remove TAG ...]
    python silt.py set ID [--title T] [--artist A] [--prompt P] [--license L] [--cleared yes|no]
    python silt.py hardware ID [ID ...] [--out DIR] [--start S] [--seconds S] [--normalize]

`hardware` (the first half of phase S6) writes items as samples an Arturia
MicroFreak can load: mono, 16-bit, 32 kHz, 24 seconds at most each, counted
against its 3.5 minute total.

Each PATH is a file or a folder (searched recursively). WAV, FLAC, MP3 and OGG
are read; everything is converted to HARDPAN's internal format, 48 kHz float32
WAV, and the original is kept unchanged beside it:

    <library>/<id>/original.<ext>    the file exactly as it came in
    <library>/<id>/audio.wav         48 kHz, float32, mono or stereo
    <library>/<id>/item.json         the metadata sidecar

The library lives in "Music\\HARDPAN Library" in your user folder unless
HARDPAN_LIBRARY or --library says otherwise. It is kept out of the repository
on purpose: a commercial recording must never end up pushed to GitHub by
accident.

Files come from your disk only. Nothing here downloads, streams or rips audio.
"""

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import soundfile as sf

from hardpan_proto import F32, SAMPLE_RATE

READABLE = {".wav", ".flac", ".mp3", ".ogg"}

# Source types, what they are called in full, and whether an item of that type
# is cleared for release unless you say otherwise. Your own recordings are
# yours. A commercial recording is not cleared until you have permission from
# both the recording's owner and the song's publisher. AI tools each have their
# own terms, so an AI file is not cleared until you have read them. Sample packs
# are sold for use in released music.
SOURCES = {
    "own": ("own recording", True),
    "ai": ("AI-generated", False),
    "commercial": ("commercial recording", False),
    "pack": ("licensed sample pack", True),
}


def default_library():
    configured = os.environ.get("HARDPAN_LIBRARY")
    return Path(configured) if configured else Path.home() / "Music" / "HARDPAN Library"


# Sample-rate conversion -------------------------------------------------------


def resample(audio, from_rate, to_rate, taps_per_phase=128, stopband_db=100.0):
    """Convert (frames, channels) audio from one sample rate to another.

    Conceptually this is three steps. Upsample by an integer `up` by putting
    up - 1 zeros between samples, low-pass the result to remove the mirror
    images that inserting zeros creates, then keep every `down`-th sample. up
    and down come from the ratio of the two rates in lowest terms: 44.1 kHz to
    48 kHz is up 160, down 147. The low-pass has to stop at the lower of the two
    Nyquist frequencies. Going down in rate, anything above the new Nyquist
    would otherwise fold back as aliasing, exactly as it does for a naive saw.

    Done literally, that would filter a signal 160 times longer than the input,
    almost all of it zeros, and then throw most of it away. The polyphase form
    computes only the samples that are kept, and for each one only the filter
    taps that land on real input samples. Every kept sample needs
    `taps_per_phase` multiplies, whatever the ratio.

    The filter is a windowed sinc with a Kaiser window, as in the drive's
    half-band filters, designed for `stopband_db` of rejection. Its length sets
    how wide the transition from passing to stopping is. With 128 taps per phase
    and 100 dB, converting 44.1 kHz to 48 kHz passes everything up to about
    19.9 kHz flat and is fully down by 22.05 kHz.
    """
    if from_rate == to_rate:
        return audio.astype(F32, copy=True)
    common = math.gcd(int(from_rate), int(to_rate))
    up, down = int(to_rate) // common, int(from_rate) // common
    taps = taps_per_phase
    length = taps * up
    rate = from_rate * up  # the rate of the conceptual zero-stuffed signal
    nyquist = min(from_rate, to_rate) / 2.0
    # Kaiser's estimates: beta for the stopband depth, and the transition width
    # a filter of this length achieves. Centring the transition just below the
    # lower Nyquist puts the full stopband at it.
    beta = 0.1102 * (stopband_db - 8.7)
    transition_hz = (stopband_db - 8.0) / (2.285 * (length - 1)) / (2 * math.pi) * rate
    cutoff = nyquist - transition_hz / 2.0
    centre = length // 2
    offsets = np.arange(length) - centre
    window = np.i0(beta * np.sqrt(1.0 - (2.0 * np.arange(length) / (length - 1) - 1.0) ** 2)) / np.i0(beta)
    ideal = (2.0 * cutoff / rate) * np.sinc(2.0 * cutoff / rate * offsets)  # np.sinc is sin(pi x) / (pi x)
    # Times `up` because the zeros dilute the signal by that factor.
    h = ideal * window * up
    # table[phase, j] is the tap that meets input sample k - j when the output
    # falls `phase` steps past input sample k in the zero-stuffed signal.
    table = h.reshape(taps, up).T

    frames, channels = audio.shape
    out_frames = (frames * up + down // 2) // down
    padded = np.concatenate([np.zeros((taps, channels), F32), audio.astype(F32), np.zeros((taps, channels), F32)])
    out = np.empty((out_frames, channels), dtype=F32)
    # Output sample m sits at q = m * down + centre in the zero-stuffed signal
    # (centre compensates the filter's delay). Its phase is q % up, and its
    # newest input sample is q // up. Outputs `up` apart share a phase, and so
    # share their weights, while their input windows move along by exactly
    # `down` samples. So each phase is a single matrix-vector product: a strided
    # view of overlapping input windows times that phase's taps, reversed to
    # line up with the windows' oldest-first order. Every output still costs
    # `taps` multiplies, but numpy does them in one call per phase.
    taps_by_phase = np.ascontiguousarray(table[:, ::-1], dtype=F32)
    down_inverse = pow(down, -1, up) if up > 1 else 0  # up and down share no factor
    for c in range(channels):
        windows = np.lib.stride_tricks.sliding_window_view(padded[:, c], taps)  # windows[r] = x[r : r + taps]
        for phase in range(up):
            # The first output m with (m * down + centre) % up == phase.
            first = ((phase - centre) * down_inverse) % up if up > 1 else 0
            if first >= out_frames:
                continue
            count = (out_frames - first + up - 1) // up
            oldest = (first * down + centre) // up + taps - (taps - 1)  # + taps for the padding
            rows = windows[oldest : oldest + (count - 1) * down + 1 : down]
            out[first::up, c] = rows @ taps_by_phase[phase]
    return out


# The library ------------------------------------------------------------------


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "item"


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_items(library):
    library = Path(library)
    items = []
    if library.exists():
        for meta in sorted(library.glob("*/item.json")):
            items.append(json.loads(meta.read_text(encoding="utf-8")))
    return items


def find(library, item_id):
    path = Path(library) / item_id / "item.json"
    if not path.exists():
        raise SystemExit(f"No item {item_id!r} in {library}")
    return json.loads(path.read_text(encoding="utf-8"))


def save(library, item):
    path = Path(library) / item["id"] / "item.json"
    path.write_text(json.dumps(item, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def to_internal(audio):
    """Mono and stereo stay as they are; anything wider is folded to stereo,
    odd channels to the left and even to the right."""
    if audio.shape[1] <= 2:
        return audio
    return np.stack([audio[:, 0::2].mean(axis=1), audio[:, 1::2].mean(axis=1)], axis=1).astype(F32)


def ingest_file(path, library, source, title=None, artist="", prompt=None, license_notes="", tags=(), cleared=None):
    """Bring one file into the library. Returns the new item, or None if the
    same file is already there."""
    path = Path(path)
    library = Path(library)
    if source not in SOURCES:
        raise ValueError(f"source must be one of {', '.join(SOURCES)}")
    digest = sha256(path)
    for item in load_items(library):
        if item["original"]["sha256"] == digest:
            print(f"  already in the library as {item['id']}: {path.name}")
            return None

    info = sf.info(str(path))
    audio, rate = sf.read(str(path), dtype="float32", always_2d=True)
    converted = resample(to_internal(audio), rate, SAMPLE_RATE)

    title = title or path.stem
    item_id = f"{slug(title)}-{digest[:8]}"
    folder = library / item_id
    folder.mkdir(parents=True, exist_ok=False)
    try:
        original_name = "original" + path.suffix.lower()
        shutil.copy2(path, folder / original_name)
        sf.write(str(folder / "audio.wav"), converted, SAMPLE_RATE, subtype="FLOAT")
        item = {
            "id": item_id,
            "title": title,
            "source_type": source,
            "artist_or_tool": artist,
            "prompt": prompt if source == "ai" else None,
            "license_notes": license_notes,
            "cleared_for_release": SOURCES[source][1] if cleared is None else bool(cleared),
            "tags": sorted(set(tags)),
            "original": {
                "file": original_name,
                "name": path.name,
                "format": info.format,
                "subtype": info.subtype,
                "sample_rate": rate,
                "channels": audio.shape[1],
                "frames": audio.shape[0],
                "seconds": round(audio.shape[0] / rate, 3),
                "sha256": digest,
            },
            "audio": {
                "file": "audio.wav",
                "sample_rate": SAMPLE_RATE,
                "channels": converted.shape[1],
                "frames": converted.shape[0],
                "seconds": round(converted.shape[0] / SAMPLE_RATE, 3),
            },
            "imported": datetime.now().isoformat(timespec="seconds"),
        }
        save(library, item)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)  # never leave a half-imported item behind
        raise
    return item


def gather(paths):
    """Files to ingest from a mix of files and folders."""
    files = []
    for p in map(Path, paths):
        if p.is_dir():
            files += sorted(f for f in p.rglob("*") if f.suffix.lower() in READABLE)
        elif p.suffix.lower() in READABLE:
            files.append(p)
        else:
            print(f"  skipping {p}: not a WAV, FLAC, MP3 or OGG file")
    return files


def search(library, text):
    needle = text.lower()

    def haystack(item):
        fields = [item["id"], item["title"], item["artist_or_tool"], item.get("prompt") or "", item["license_notes"]]
        return " ".join(fields + item["tags"]).lower()

    return [item for item in load_items(library) if needle in haystack(item)]


# Hardware export ----------------------------------------------------------------

# What the Arturia MicroFreak accepts as a user sample: mono, 16-bit, 32 kHz, at
# most 24 seconds each, and 3.5 minutes across everything loaded.
HARDWARE_RATE = 32000
HARDWARE_MAX_SECONDS = 24.0
HARDWARE_BUDGET_SECONDS = 210.0
FADE_SECONDS = 0.01


def to_hardware(audio, start=0.0, seconds=HARDWARE_MAX_SECONDS, normalize=False, seed=0):
    """Turn library audio (48 kHz float, mono or stereo) into the samples of one
    hardware file: mono, 32 kHz, 16-bit, at most 24 s. Returns int16.

    Stereo is folded by averaging the channels. That loses anything the two
    channels carry in opposite phase, which is the price of a mono sampler.

    Going from 48 kHz to 32 kHz moves Nyquist down to 16 kHz, so the resampler
    removes everything above that first; otherwise it would fold back as
    aliasing.

    If the excerpt is cut out of something longer, its end gets a 10 ms fade, and
    so does its start when that is not the start of the item: a cut straight
    through a waveform is a step, and a step is a click.

    16 bits has steps of 1/32768. Rounding to them adds an error that follows
    the signal, which on a quiet fade is heard as distortion. Adding noise of
    one step's width first (triangular dither) turns that error into a steady
    hiss about 96 dB down, which the ear ignores.
    """
    seconds = min(float(seconds), HARDWARE_MAX_SECONDS)
    mono = audio.mean(axis=1, keepdims=True).astype(F32)
    first = max(0, int(round(start * SAMPLE_RATE)))
    last = min(len(mono), first + int(round(seconds * SAMPLE_RATE)))
    excerpt = mono[first:last].copy()
    fade = min(int(FADE_SECONDS * SAMPLE_RATE), len(excerpt) // 2)
    if fade:
        ramp = (0.5 - 0.5 * np.cos(np.pi * np.arange(fade) / fade)).astype(F32)[:, None]
        if first > 0:
            excerpt[:fade] *= ramp
        if last < len(mono):
            excerpt[-fade:] *= ramp[::-1]
    out = resample(excerpt, SAMPLE_RATE, HARDWARE_RATE)[:, 0].astype(np.float64)
    out = out[: int(HARDWARE_MAX_SECONDS * HARDWARE_RATE)]  # the rounding in resample can add a sample
    peak = float(np.max(np.abs(out))) if len(out) else 0.0
    # One step of headroom for the dither, so full scale cannot wrap around.
    ceiling = 1.0 - 2.0 / 32768.0
    if peak > 0.0 and (normalize or peak > ceiling):
        out *= ceiling / peak
    rng = np.random.default_rng(seed)
    dither = rng.random(len(out)) - rng.random(len(out))  # triangular, one step wide each way
    return np.clip(np.round(out * 32768.0 + dither), -32768, 32767).astype(np.int16)


def hardware_export(library, item_ids, out_dir, start=0.0, seconds=HARDWARE_MAX_SECONDS, normalize=False):
    """Write items as MicroFreak-ready WAV files into `out_dir`, counting them,
    and whatever is already there, against the 3.5 minute budget. An item that
    would go over the budget is skipped. Returns one dict per item."""
    library, out_dir = Path(library), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    used = sum(sf.info(str(f)).duration for f in sorted(out_dir.glob("*.wav")))
    results = []
    for item_id in item_ids:
        item = find(library, item_id)
        audio, rate = sf.read(str(library / item_id / item["audio"]["file"]), dtype="float32", always_2d=True)
        assert rate == SAMPLE_RATE, f"{item_id}: library audio should be {SAMPLE_RATE} Hz, found {rate}"
        samples = to_hardware(audio, start, seconds, normalize)
        length = len(samples) / HARDWARE_RATE
        path = out_dir / f"{slug(item['title'])[:24]}-{item['original']['sha256'][:4]}.wav"
        replacing = sf.info(str(path)).duration if path.exists() else 0.0
        result = {"id": item_id, "path": path, "seconds": length, "written": False,
                  "cleared": item["cleared_for_release"],
                  "truncated": start + length < item["audio"]["seconds"] - 0.001}
        if len(samples) == 0:
            result["reason"] = "nothing to export from that start time"
        elif used - replacing + length > HARDWARE_BUDGET_SECONDS + 1e-6:
            result["reason"] = (f"{length:.1f} s would not fit: "
                                f"{HARDWARE_BUDGET_SECONDS - used + replacing:.1f} s of the budget is left")
        else:
            sf.write(str(path), samples, HARDWARE_RATE, subtype="PCM_16")
            used += length - replacing
            result["written"] = True
        result["used"] = used
        results.append(result)
    return results


# Command line -------------------------------------------------------------------


def print_table(items):
    if not items:
        print("Nothing found.")
        return
    rows = [
        (
            item["id"],
            item["title"][:32],
            SOURCES[item["source_type"]][0],
            f"{item['audio']['seconds']:.1f} s",
            "yes" if item["cleared_for_release"] else "NO",
            ", ".join(item["tags"]),
        )
        for item in items
    ]
    header = ("ID", "TITLE", "SOURCE", "LENGTH", "CLEARED", "TAGS")
    widths = [max(len(str(r[i])) for r in rows + [header]) for i in range(len(header))]
    for row in [header] + rows:
        print("  ".join(str(v).ljust(w) for v, w in zip(row, widths)).rstrip())


def show(item):
    print(json.dumps(item, indent=2, ensure_ascii=False))


def yes_no(text):
    if text.lower() in ("yes", "y", "true", "1"):
        return True
    if text.lower() in ("no", "n", "false", "0"):
        return False
    raise argparse.ArgumentTypeError("use yes or no")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="silt.py", description="SILT: HARDPAN's library of outside audio")
    parser.add_argument("--library", type=Path, default=None, help="library folder (default: Music\\HARDPAN Library)")
    commands = parser.add_subparsers(dest="command", required=True)

    ingest = commands.add_parser("ingest", help="import files or folders")
    ingest.add_argument("paths", nargs="+")
    ingest.add_argument("--source", required=True, choices=SOURCES, help="own, ai, commercial or pack")
    ingest.add_argument("--title", help="title (default: the file name; only for a single file)")
    ingest.add_argument("--artist", default="", help="artist, or the tool for AI-generated audio")
    ingest.add_argument("--prompt", help="the prompt, for AI-generated audio")
    ingest.add_argument("--license", default="", help="license notes")
    ingest.add_argument("--tag", action="append", default=[], help="a tag; repeat for more")
    ingest.add_argument("--cleared", type=yes_no, help="override the cleared-for-release default: yes or no")

    listing = commands.add_parser("list", help="list the library")
    listing.add_argument("--source", choices=SOURCES)
    listing.add_argument("--tag")

    finding = commands.add_parser("search", help="search titles, artists, prompts, notes and tags")
    finding.add_argument("text")

    showing = commands.add_parser("show", help="show one item's metadata")
    showing.add_argument("id")

    tagging = commands.add_parser("tag", help="add or remove tags")
    tagging.add_argument("id")
    tagging.add_argument("--add", nargs="+", default=[])
    tagging.add_argument("--remove", nargs="+", default=[])

    setting = commands.add_parser("set", help="change an item's metadata")
    setting.add_argument("id")
    setting.add_argument("--title")
    setting.add_argument("--artist")
    setting.add_argument("--prompt")
    setting.add_argument("--license")
    setting.add_argument("--cleared", type=yes_no)

    hardware = commands.add_parser("hardware", help="export items as MicroFreak samples: mono 16-bit 32 kHz, 24 s at most")
    hardware.add_argument("ids", nargs="+")
    hardware.add_argument("--out", type=Path, default=Path("exports") / "microfreak", help="folder to write into")
    hardware.add_argument("--start", type=float, default=0.0, help="where in each item to start, in seconds")
    hardware.add_argument("--seconds", type=float, default=HARDWARE_MAX_SECONDS, help="length to take, 24 at most")
    hardware.add_argument("--normalize", action="store_true", help="raise each file's peak to full scale")

    args = parser.parse_args(argv)
    library = args.library or default_library()

    if args.command == "ingest":
        files = gather(args.paths)
        if args.title and len(files) != 1:
            raise SystemExit("--title only works when importing a single file")
        print(f"Importing {len(files)} file(s) into {library}")
        for f in files:
            try:
                item = ingest_file(f, library, args.source, args.title, args.artist, args.prompt,
                                   args.license, args.tag, args.cleared)
            except (RuntimeError, sf.LibsndfileError) as error:  # unreadable or corrupt files
                print(f"  could not read {f.name}: {error}")
                continue
            if item:
                cleared = "cleared" if item["cleared_for_release"] else "NOT cleared for release"
                print(f"  {item['id']}: {item['original']['seconds']:.1f} s at {item['original']['sample_rate']} Hz"
                      f" -> 48 kHz float, {cleared}")
    elif args.command == "list":
        items = load_items(library)
        if args.source:
            items = [i for i in items if i["source_type"] == args.source]
        if args.tag:
            items = [i for i in items if args.tag in i["tags"]]
        print_table(items)
    elif args.command == "search":
        print_table(search(library, args.text))
    elif args.command == "show":
        show(find(library, args.id))
    elif args.command == "tag":
        item = find(library, args.id)
        item["tags"] = sorted((set(item["tags"]) | set(args.add)) - set(args.remove))
        save(library, item)
        print(f"{item['id']}: {', '.join(item['tags']) or '(no tags)'}")
    elif args.command == "set":
        item = find(library, args.id)
        for field, value in (("title", args.title), ("artist_or_tool", args.artist),
                             ("prompt", args.prompt), ("license_notes", args.license)):
            if value is not None:
                item[field] = value
        if args.cleared is not None:
            item["cleared_for_release"] = args.cleared
            if args.cleared and item["source_type"] in ("commercial", "ai"):
                print("Marked cleared. Make sure you hold the permissions or terms that allow release.")
        save(library, item)
        show(item)
    elif args.command == "hardware":
        results = hardware_export(library, args.ids, args.out, args.start, args.seconds, args.normalize)
        for r in results:
            if not r["written"]:
                print(f"  SKIPPED {r['id']}: {r['reason']}")
                continue
            notes = [f"{r['seconds']:.1f} s"]
            if r["truncated"]:
                notes.append("an excerpt, not the whole item")
            if not r["cleared"]:
                notes.append("NOT cleared for release")
            print(f"  {r['path'].name}: {', '.join(notes)}")
        used = results[-1]["used"]
        print(f"{args.out}: {used:.1f} s of {HARDWARE_BUDGET_SECONDS:.0f} s used, "
              f"{HARDWARE_BUDGET_SECONDS - used:.1f} s left")


if __name__ == "__main__":
    main(sys.argv[1:])
