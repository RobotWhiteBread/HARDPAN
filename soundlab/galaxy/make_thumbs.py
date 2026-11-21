"""
Makes a tiny waveform picture (32 peak levels) for every sample so the Sound
Galaxy can show it on hover. Resumable: rerun and it continues.
Writes galaxy/data/thumbs_cache.tsv; make_galaxy.py turns it into galaxy_thumbs.js.

  python make_thumbs.py
"""
import argparse, csv, os, time
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import soundfile as sf

POINTS = 32
MAX_SECONDS = 12.0


def thumb(path):
    try:
        info = sf.info(path)
        frames = int(min(info.frames, info.samplerate * MAX_SECONDS))
        y, _ = sf.read(path, frames=frames, dtype="float32", always_2d=True)
        y = np.abs(y).max(axis=1)
        if not y.size:
            return path, "0" * (POINTS * 2)
        chunks = np.array_split(y, POINTS)
        pk = np.array([c.max() if c.size else 0 for c in chunks])
        pk = pk / (pk.max() + 1e-9)
        pk = np.sqrt(pk)  # easier to see quiet tails
        return path, "".join(f"{int(v * 255):02x}" for v in pk)
    except Exception:
        return path, "0" * (POINTS * 2)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("--features", default=r"C:\HARDPAN\studio_setup\samples\_library\features.csv")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    a = ap.parse_args()
    cache = os.path.join(here, "data", "thumbs_cache.tsv")
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    done = set()
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as fh:
            done = {line.split("\t", 1)[0] for line in fh}
    with open(a.features, newline="", encoding="utf-8") as fh:
        todo = [r["Path"] for r in csv.DictReader(fh)
                if not r["error"] and r["Path"] not in done and not r["Path"].rsplit("\\", 1)[-1].startswith("._")]
    print(f"{len(done)} waveforms already made, {len(todo)} to go")
    if not todo:
        return
    t0 = time.time()
    with open(cache, "a", encoding="utf-8") as out, ProcessPoolExecutor(a.workers) as ex:
        for n, (p, h) in enumerate(ex.map(thumb, todo, chunksize=64), 1):
            out.write(p + "\t" + h + "\n")
            if n % 5000 == 0 or n == len(todo):
                out.flush()
                rate = n / (time.time() - t0)
                print(f"  {n} of {len(todo)}  ({rate:.0f}/s, about {(len(todo) - n) / rate / 60:.0f} min left)")


if __name__ == "__main__":
    main()
