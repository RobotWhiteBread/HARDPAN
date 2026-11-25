"""
soundlab setup: find the sample folders on this computer and write soundlab.json for you.

  python soundlab.py setup                 look in the usual places, then ask about each folder it finds
  python soundlab.py setup --yes           take every folder it finds without asking
  python soundlab.py setup --deep          also look through every drive (slower)
  python soundlab.py setup --mine FOLDER   add a folder of your own recordings, labeled "Mine"

It only reads folder and file names. It never moves, copies or uploads anything.
An existing soundlab.json is kept as soundlab.json.bak and its other settings are carried over.
"""
import json, os, string, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import soundlab_config as SC

AUDIO = {".wav", ".aif", ".aiff", ".flac", ".mp3", ".ogg"}
PROJECT = {".rpp": "REAPER", ".als": "Ableton Live", ".flp": "FL Studio", ".logicx": "Logic", ".ptx": "Pro Tools", ".song": "Studio One", ".cpr": "Cubase", ".bwproject": "Bitwig"}
SKIP = {"windows", "program files", "program files (x86)", "programdata", "appdata", "$recycle.bin", "system volume information",
        "node_modules", ".git", "__pycache__", "library", "system", "applications", "private", "cache", "caches", "temp", "tmp",
        "_library", "steamapps", "onedrivetemp", "recovery", "msocache", "perflogs"}
CONTAINERS = {"music", "documents", "desktop", "downloads", "users", "samples", "audio", "home"}  # too general to be a folder on their own
MIN_SOUNDS = 25


def usual_places():
    home = os.path.expanduser("~")
    places = [os.path.join(home, p) for p in ("Music", "Documents", "Desktop", "Downloads", "Splice", "Samples")]
    places += [os.path.join(home, "Documents", p) for p in ("Native Instruments", "Image-Line", "Ableton")]
    places += [r"C:\Users\Public\Documents", r"C:\ProgramData\Ableton", "/Users/Shared"]
    return [p for p in places if os.path.isdir(p)]


def drives():
    if os.name == "nt":
        return [f"{d}:\\" for d in string.ascii_uppercase if os.path.isdir(f"{d}:\\")]
    return [p for p in ("/Volumes", "/media", "/mnt") if os.path.isdir(p)]


def survey(roots, max_depth=6, budget_s=240):
    """Count audio files under each folder (depth-limited, time-limited) and note DAW project folders."""
    counts, projects, start = {}, {}, time.time()
    seen = set()
    for root in roots:
        stack = [(os.path.normpath(root), 0)]
        while stack:
            if time.time() - start > budget_s:
                print("  (stopping the search early to save time; add more folders with --mine or by hand)")
                return counts, projects
            path, depth = stack.pop()
            key = os.path.normcase(path)
            if key in seen:
                continue
            seen.add(key)
            try:
                entries = list(os.scandir(path))
            except OSError:
                continue
            n = 0
            for e in entries:
                low = e.name.lower()
                try:
                    if e.is_dir(follow_symlinks=False):
                        if low.endswith(".logicx"):
                            projects.setdefault(path, set()).add("Logic")
                        elif depth < max_depth and low not in SKIP and not low.startswith("."):
                            stack.append((e.path, depth + 1))
                    else:
                        ext = os.path.splitext(low)[1]
                        if ext in AUDIO:
                            n += 1
                        elif ext in PROJECT:
                            projects.setdefault(path, set()).add(PROJECT[ext])
                except OSError:
                    pass
            if n:
                counts[path] = n
    return counts, projects


def roll_up(counts, roots):
    """Sum each folder's sounds into its ancestors (up to the search root), then keep the highest folder that is specific enough."""
    tops = {os.path.normcase(os.path.normpath(r)) for r in roots}
    total = {}
    for path, n in counts.items():
        p = path
        while True:
            total[p] = total.get(p, 0) + n
            parent = os.path.dirname(p)
            if parent == p or os.path.normcase(p) in tops:
                break
            p = parent
    def specific(p):
        name = os.path.basename(p).lower()
        return name and name not in CONTAINERS and os.path.dirname(p) != p and not os.path.ismount(p)
    picks = []
    for p in sorted(total, key=lambda x: (x.count(os.sep), x)):
        if total[p] < MIN_SOUNDS or not specific(p):
            continue
        if any(p.startswith(q + os.sep) for q in picks):
            continue
        picks.append(p)
    return [(p, total[p]) for p in picks]


def ask(q, default=True):
    a = input(q + (" [Y/n] " if default else " [y/N] ")).strip().lower()
    return default if not a else a.startswith("y")


def main():
    args = sys.argv[1:]
    yes, deep = "--yes" in args, "--deep" in args
    mine = args[args.index("--mine") + 1] if "--mine" in args and args.index("--mine") + 1 < len(args) else None
    cfg_path = SC.config_path()
    old = {}
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf-8-sig") as fh:
            old = json.load(fh)
    have = {os.path.normcase(SC._expand(s["path"])) for s in old.get("sample_folders", [])}

    roots = usual_places() + (drives() if deep else [])
    print("Looking for sounds in:\n  " + "\n  ".join(roots))
    counts, projects = survey(roots, budget_s=600 if deep else 240)
    if mine:
        have.add(os.path.normcase(os.path.abspath(os.path.expanduser(mine))))
    found = [(p, n) for p, n in roll_up(counts, roots) if os.path.normcase(p) not in have
             and not any(os.path.normcase(p).startswith(h + os.sep) for h in have)]
    folders = list(old.get("sample_folders", []))
    if not found:
        print("No new sample folders found." + ("" if deep else " Try --deep to look through every drive."))
    else:
        print(f"\nFound {len(found)} folder(s) with sounds:")
        for p, n in found:
            name = os.path.basename(p)
            if yes:
                print(f"  {p}  ({n:,} sounds)")
            if yes or ask(f"  {p}  ({n:,} sounds)  add it?"):
                folders.append({"name": name, "path": p.replace("\\", "/")})
    if mine:
        mp = os.path.abspath(os.path.expanduser(mine))
        if os.path.isdir(mp) and not any(os.path.normcase(SC._expand(f["path"])) == os.path.normcase(mp) for f in folders):
            folders.append({"name": "Mine", "path": mp.replace("\\", "/")})
            print(f"Added your recordings: {mp} (they show as 'Mine' in Color by vendor)")
    if projects and not old.get("project_folders"):
        kinds = sorted({k for v in projects.values() for k in v})
        print(f"\nFound {len(projects)} folder(s) with {', '.join(kinds)} projects (used for Used vs owned; REAPER is read today).")
        rpp = sorted(p for p, v in projects.items() if "REAPER" in v)
        if rpp:
            common = os.path.commonpath(rpp) if len(rpp) > 1 else os.path.dirname(rpp[0])
            if yes or ask(f"  Use {common} as your project folder?"):
                old["project_folders"] = [common.replace("\\", "/")]

    if not folders:
        raise SystemExit("Nothing to save. Add a folder with --mine FOLDER, or copy soundlab.example.json and edit it.")
    new = dict(old, sample_folders=folders)
    for k in ("library", "projects", "downloads"):
        new.setdefault(k, {"library": "~/Music/Samples/_library", "projects": "~/Music/soundlab_projects", "downloads": "~/Downloads"}[k])
    if os.path.exists(cfg_path):
        os.replace(cfg_path, cfg_path + ".bak")
    with open(cfg_path, "w", encoding="utf-8") as fh:
        json.dump(new, fh, indent=2)
    print(f"\nSaved {len(folders)} sample folder(s) to {cfg_path}")
    print("Next: python soundlab.py all   (catalog, measure and open the Galaxy)")


if __name__ == "__main__":
    main()
