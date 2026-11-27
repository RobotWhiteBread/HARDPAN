"""
Serve the Sound Galaxy from your own computer at http://127.0.0.1 so the page can load
sounds into memory. That is what the beat playground and in-time previews need; opened as
a plain file, browsers only allow simple one-at-a-time playback.

Only this computer can connect (the server listens on 127.0.0.1), and it only hands out
audio files that live inside the sample folders and library listed in soundlab.json.

  python serve.py [--port 8765] [--no-open] [--page voice.html]
"""
import argparse, http.server, mimetypes, os, sys, urllib.parse, webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import soundlab_config as SC

AUDIO = {".wav", ".aif", ".aiff", ".flac", ".mp3", ".ogg"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--page", default="index.html", help="page to open, for example voice.html")
    a = ap.parse_args()
    cfg = SC.load()
    try:                                            # list older Mix Check reports and Mix Stages in the Mixes menu
        sys.path.insert(0, os.path.join(os.path.dirname(HERE), "mixmath"))
        import mix_index
        mix_index.backfill(cfg.get("projects"))
    except Exception:
        pass
    norm = lambda p: os.path.normcase(os.path.realpath(p)).rstrip("\\/") + os.sep
    roots = [norm(s["path"]) for s in cfg["sample_folders"]] + [norm(cfg["library"])]

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kw):
            super().__init__(*args, directory=HERE, **kw)

        def log_message(self, *args):
            pass

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path != "/audio":
                return super().do_GET()
            p = urllib.parse.parse_qs(u.query).get("p", [""])[0]
            real = os.path.normcase(os.path.realpath(p))
            if (os.path.splitext(real)[1].lower() not in AUDIO or not any(real.startswith(r) for r in roots)
                    or not os.path.isfile(real)):
                return self.send_error(404)
            with open(real, "rb") as fh:
                data = fh.read()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(real)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=3600")
            self.end_headers()
            self.wfile.write(data)

    srv = None
    for port in range(a.port, a.port + 20):
        try:
            srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if srv is None:
        raise SystemExit("No free port found for the Galaxy server.")
    url = f"http://127.0.0.1:{srv.server_address[1]}/{a.page}"
    print(f"Sound Galaxy running at {url}")
    print("Leave this window open while you use it. Press Ctrl+C to stop.")
    if not a.no_open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
