"""
soundlab: one command for every step.

  python soundlab.py all              build the library, measure every sound, make waveforms, open the Galaxy
  python soundlab.py build            step 1: catalog your sample folders (and browse folders for REAPER)
  python soundlab.py analyze [--test] step 2: measure every sound (resumable; --test does 300)
  python soundlab.py galaxy [--waveforms] [--no-open]
                                      step 3: make the Sound Galaxy data and open it in your browser
  python soundlab.py serve [--port N] open the Galaxy through a private local server: needed for the
                                      beat playground and in-time previews (leave the window open)
  python soundlab.py check SONG.wav [--refs FOLDER] [--kick K.wav --bass B.wav]
                                      mix report with a 3D spectrogram
  python soundlab.py audit            check your REAPER projects and find alternatives for every sample
  python soundlab.py cart             turn the newest saved cart into a Cart folder for REAPER's Media Explorer
  python soundlab.py paths            write reaper/soundlab_paths.lua from soundlab.json
"""
import csv, datetime, glob, os, subprocess, sys, webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import soundlab_config as SC


def run(script, *args):
    """Run one tool in its own process (needed on Windows for the multi-core steps)."""
    r = subprocess.run([sys.executable, os.path.join(HERE, script)] + [str(a) for a in args])
    if r.returncode != 0:
        raise SystemExit(f"{script} stopped with an error (see above).")


def open_galaxy():
    page = os.path.join(HERE, "galaxy", "index.html")
    print("Opening", page)
    webbrowser.open("file:///" + page.replace("\\", "/").lstrip("/"))


def cart_folder():
    cfg = SC.load()
    carts = sorted(glob.glob(os.path.join(cfg["downloads"], "soundlab_cart*.txt")), key=os.path.getmtime)
    if not carts:
        raise SystemExit(f"No soundlab_cart.txt in {cfg['downloads']}. Fill the cart in the Galaxy and press Save cart.")
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H%M")
    dest = os.path.join(cfg["library"], "Cart", stamp)
    os.makedirs(dest, exist_ok=True)
    n = bad = 0
    with open(carts[-1], encoding="utf-8") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            src = r["path"]
            name = f"{r['type']} - {SC.basename(src)}"
            try:
                os.link(src, os.path.join(dest, name))       # hard link: no extra disk space
                n += 1
            except OSError:
                try:
                    import shutil
                    shutil.copy2(src, os.path.join(dest, name))  # different drive: copy instead
                    n += 1
                except OSError:
                    bad += 1
    print(f"{n} sounds from {SC.basename(carts[-1])} are in {dest}" + (f" ({bad} not found)" if bad else ""))
    print("Open that folder in REAPER's Media Explorer (Ctrl+Alt+X) to drag them into a session.")


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return
    cmd, rest = sys.argv[1], sys.argv[2:]
    if cmd == "build":
        run("build_library.py", *rest)
    elif cmd == "analyze":
        run("analyze_samples.py", *(["--limit", "300"] if "--test" in rest else [a for a in rest if a != "--test"]))
    elif cmd == "galaxy":
        if "--waveforms" in rest:
            run(os.path.join("galaxy", "make_thumbs.py"))
        run(os.path.join("galaxy", "make_galaxy.py"))
        if "--no-open" not in rest:
            open_galaxy()
    elif cmd == "serve":
        subprocess.run([sys.executable, os.path.join(HERE, "galaxy", "serve.py")] + rest)
    elif cmd == "check":
        run(os.path.join("mixmath", "mix_check.py"), *rest)
    elif cmd == "audit":
        run(os.path.join("reaper", "project_audit.py"), *rest)
    elif cmd == "cart":
        cart_folder()
    elif cmd == "paths":
        print("Wrote", SC.write_reaper_paths())
    elif cmd == "all":
        SC.write_reaper_paths()
        run("build_library.py")
        run("analyze_samples.py")
        run(os.path.join("galaxy", "make_thumbs.py"))
        run(os.path.join("galaxy", "make_galaxy.py"))
        if "--no-open" not in rest:
            open_galaxy()
    else:
        print(f"Unknown command: {cmd}\n")
        print(__doc__)


if __name__ == "__main__":
    main()
