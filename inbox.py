#!/usr/bin/env python3
"""Drop -> Decide: keyboard-first inbox processor for a DJ library.

Scans playlists/_inbox/, shows track metadata (parsed filename + tags),
and moves each track into its playlist folder with a single keypress.
Run: python inbox.py  ->  http://localhost:8791
"""
import argparse
import json
import os
import re
import shutil
import sys
import urllib.parse
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    import mutagen
    from mutagen.easyid3 import EasyID3
    EasyID3.RegisterTextKey("initialkey", "TKEY")  # not mapped by default
except ImportError:  # tags degrade gracefully; placement still works
    mutagen = None

TOOL_DIR = Path(__file__).resolve().parent


def load_config(tool_dir=TOOL_DIR):
    """Optional config.json next to inbox.py, e.g. {"music_root": "~/Music/Music/Music"}."""
    cfg = tool_dir / "config.json"
    if not cfg.is_file():
        return {}
    try:
        return json.loads(cfg.read_text())
    except json.JSONDecodeError as e:
        print(f"config.json: ignoring invalid JSON ({e})", file=sys.stderr)
        return {}


def resolve_music_root(cli=None, env=None, config=None):
    """Precedence: --music-root flag > INBOX_MUSIC_ROOT env > config.json > tool's parent dir."""
    env = os.environ if env is None else env
    config = load_config() if config is None else config
    raw = cli or env.get("INBOX_MUSIC_ROOT") or config.get("music_root")
    return Path(raw).expanduser().resolve() if raw else TOOL_DIR.parent


MUSIC_ROOT = resolve_music_root()
PLAYLISTS = MUSIC_ROOT / "playlists"
PORT = int(os.environ.get("INBOX_PORT", "8791"))

AUDIO_EXTS = {".mp3", ".wav", ".aif", ".aiff", ".flac", ".m4a"}
MIME = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".aif": "audio/x-aiff",
        ".aiff": "audio/x-aiff", ".flac": "audio/flac", ".m4a": "audio/mp4"}
LOG_NAME = "decisions.jsonl"

# key -> (label, path under playlists/, cheat-sheet group)
# Used when config.json has no "destinations" (or none valid).
DEFAULT_DESTINATIONS = {
    "1": ("classics_never_fail", "open_format/classics_never_fail", "Open Format"),
    "2": ("current_pop_hip_hop_rnb", "open_format/current_pop_hip_hop_rnb", "Open Format"),
    "3": ("latin", "open_format/latin", "Open Format"),
    "4": ("80s", "open_format/80s", "Open Format"),
    "5": ("90s", "open_format/90s", "Open Format"),
    "6": ("2000s", "open_format/2000s", "Open Format"),
    "7": ("2010s", "open_format/2010s", "Open Format"),
    "8": ("requests", "open_format/requests", "Open Format"),
    "q": ("dubstep", "electronic/dubstep", "Electronic — genre"),
    "w": ("house", "electronic/house", "Electronic — genre"),
    "e": ("big_room", "electronic/big_room", "Electronic — genre"),
    "r": ("hardstyle", "electronic/hardstyle", "Electronic — genre"),
    "t": ("melodic_dubstep", "electronic/melodic_dubstep", "Electronic — genre"),
    "a": ("warm_up", "electronic/warm_up", "Electronic — energy"),
    "s": ("peak", "electronic/peak", "Electronic — energy"),
    "d": ("closing", "electronic/closing", "Electronic — energy"),
    "z": ("samples", "tools/samples", "Tools"),
    "x": ("acapellas", "tools/acapellas", "Tools"),
    "c": ("edits_transitions", "tools/edits_transitions", "Tools"),
    "v": ("review_later", "_inbox/review_later", ""),
}

# Single-char keys the UI already owns (see index.html keydown handler).
RESERVED_KEYS = {" ", ",", "u", "/"}

VALID_SUBS = ("", "review_later")


def _validate_destination(key, spec):
    """One config "destinations" entry -> (label, path, group) or None (+ warning)."""
    if not (isinstance(key, str) and len(key) == 1
            and key == key.lower() and key not in RESERVED_KEYS):
        print(f"config.json: bad destination key {key!r} "
              "(need one lowercase, non-reserved char)", file=sys.stderr)
        return None
    if not isinstance(spec, dict):
        print(f"config.json: destination {key!r} must be an object", file=sys.stderr)
        return None
    label, rel = spec.get("label"), spec.get("path")
    group = spec.get("group", "")
    if not isinstance(label, str) or not label.strip():
        print(f"config.json: destination {key!r} needs a non-empty \"label\"",
              file=sys.stderr)
        return None
    p = Path(rel) if isinstance(rel, str) else None
    if p is None or not rel.strip() or p.is_absolute() or ".." in p.parts:
        print(f"config.json: destination {key!r} has an unsafe \"path\": {rel!r}",
              file=sys.stderr)
        return None
    return label, rel, group if isinstance(group, str) else ""


def load_destinations(tool_dir=TOOL_DIR):
    """Destinations from config.json "destinations" (validated), else the defaults.

    Config form (insertion order = display order):
      "destinations": {"1": {"label": "...", "path": "open_format/x",
                             "group": "Open Format"}}
    Invalid entries are skipped with a warning; if none survive, defaults apply.
    """
    raw = load_config(tool_dir).get("destinations")
    if raw is None:
        return dict(DEFAULT_DESTINATIONS)
    dests = {}
    if isinstance(raw, dict):
        for key, spec in raw.items():
            entry = _validate_destination(key, spec)
            if entry:
                dests[key] = entry
    if not dests:
        print("config.json: no valid \"destinations\" — using built-in defaults",
              file=sys.stderr)
        return dict(DEFAULT_DESTINATIONS)
    return dests


# ---------- core logic (importable/testable) ----------

def _humanize(s):
    return re.sub(r"\s+", " ", s.replace("_", " ")).strip()


def _split_version(rest):
    """Pop trailing (parenthesized) bits into a version string.
    Keeps feat./ft./with groups in the title."""
    version_bits = []
    title = rest
    while True:
        m = re.search(r"\s*\(([^()]*)\)\s*$", title)
        if not m:
            break
        bit = m.group(1)
        if bit.lower().startswith(("feat", "ft.", "ft ", "with")):
            break
        version_bits.insert(0, bit)
        title = title[:m.start()]
    return title, " ".join(version_bits)


def parse_filename(name):
    """Three dialects: 'Artist - Title (V)', 'Artist_-_Title_(V)', freeform."""
    stem = Path(name).stem
    if "_-_" in stem:                      # Beatport
        artist, rest = stem.split("_-_", 1)
        title, version = _split_version(rest)
    elif " - " in stem:                    # DJ City / space-dash
        parts = stem.split(" - ")
        artist = parts[0]
        if len(parts) > 2:
            title, version = parts[1], " - ".join(parts[2:])
            title, lead = _split_version(title)
            version = " ".join(b for b in (lead, version) if b)
        else:
            title, version = _split_version(parts[1])
    else:                                  # freeform: no reliable artist
        return {"artist": "", "title": _humanize(stem), "version": ""}
    return {"artist": _humanize(artist), "title": _humanize(title),
            "version": _humanize(version)}


def read_tags(path):
    """BPM/key/genre/title/artist/duration from tags; {} if unreadable."""
    if mutagen is None:
        return {}
    try:
        audio = mutagen.File(str(path), easy=True)
        if audio is None or audio.tags is None:
            return {}
        def first(k):
            v = audio.tags.get(k)
            return str(v[0]).strip() if v else ""
        tags = {"bpm": first("bpm"), "key": first("initialkey"),
                "genre": first("genre"), "title": first("title"),
                "artist": first("artist"), "date": first("date")}
        length = getattr(getattr(audio, "info", None), "length", None)
        out = {k: v for k, v in tags.items() if v}
        if length:
            out["duration"] = round(length)
        return out
    except Exception:
        return {}


def track_info(path):
    info = {"file": path.name, **parse_filename(path.name)}
    tags = read_tags(path)
    if not info["artist"]:  # freeform filename: tags are the only metadata source
        for k in ("title", "artist"):
            if tags.get(k):
                info[k] = tags[k]
    for k in ("bpm", "key", "genre", "duration", "date"):
        info[k] = tags.get(k, "")
    info["tagged"] = bool(tags)
    return info


def _inbox_dir(playlists, sub):
    if sub not in VALID_SUBS:
        raise ValueError(f"bad subdir: {sub!r}")
    return playlists / "_inbox" / sub


def list_tracks(playlists, sub=""):
    d = _inbox_dir(playlists, sub)
    if not d.is_dir():
        return []
    files = sorted(p for p in d.iterdir()
                   if p.is_file() and p.suffix.lower() in AUDIO_EXTS)
    return [track_info(p) for p in files]


def _log_path(playlists):
    return playlists / "_inbox" / LOG_NAME


def _append_log(playlists, entry):
    with open(_log_path(playlists), "a") as f:
        f.write(json.dumps(entry) + "\n")


def place_file(playlists, name, key, sub="", destinations=None):
    destinations = DEFAULT_DESTINATIONS if destinations is None else destinations
    if key not in destinations:
        raise ValueError(f"unknown destination key: {key!r}")
    if Path(name).name != name:
        raise ValueError(f"not a plain filename: {name!r}")
    src_dir = _inbox_dir(playlists, sub)
    src = src_dir / name
    if not src.is_file():
        raise FileNotFoundError(name)

    label, rel = destinations[key][:2]
    dest_dir = playlists / rel
    dest_dir.mkdir(parents=True, exist_ok=True)

    final = name
    stem, ext = src.stem, src.suffix
    n = 2
    while (dest_dir / final).exists():
        final = f"{stem}-{n}{ext}"
        n += 1
    shutil.move(str(src), str(dest_dir / final))

    entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "file": name, "placed_as": final,
             "from": "_inbox" + ("/" + sub if sub else ""), "to": rel}
    _append_log(playlists, entry)
    return {"file": name, "placed_as": final, "to": rel, "label": label}


def undo_last(playlists):
    log = _log_path(playlists)
    if not log.is_file():
        return None
    lines = log.read_text().splitlines()
    if not lines:
        return None
    entry = json.loads(lines[-1])
    placed_as = entry.get("placed_as", entry["file"])
    src = playlists / entry["to"] / placed_as
    dest_dir = playlists / entry["from"]
    dest_dir.mkdir(parents=True, exist_ok=True)
    if src.is_file():
        shutil.move(str(src), str(dest_dir / entry["file"]))
    log.write_text(("\n".join(lines[:-1]) + "\n") if len(lines) > 1 else "")
    return entry


# ---------- HTTP server ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "InboxTool/1.0"

    def log_message(self, *args):  # quiet
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status, msg):
        self._json({"error": msg}, status)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(url.query)
        sub = qs.get("dir", [""])[0]
        if url.path == "/":
            self._serve_index()
        elif url.path == "/api/config":
            dests = load_destinations()  # re-read each request: edits need no restart
            self._json({"destinations": {k: {"label": l, "path": p, "group": g}
                                         for k, (l, p, g) in dests.items()}})
        elif url.path == "/api/tracks":
            try:
                self._json({"tracks": list_tracks(PLAYLISTS, sub)})
            except ValueError as e:
                self._error(400, str(e))
        elif url.path.startswith("/audio/"):
            name = urllib.parse.unquote(url.path[len("/audio/"):])
            self._serve_audio(name, sub)
        else:
            self._error(404, "not found")

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._error(400, "bad request body")
        try:
            if url.path == "/api/place":
                res = place_file(PLAYLISTS, body.get("file", ""),
                                 body.get("dest", ""), body.get("dir", ""),
                                 load_destinations())
                self._json(res)
            elif url.path == "/api/undo":
                self._json({"undone": undo_last(PLAYLISTS)})
            else:
                self._error(404, "not found")
        except ValueError as e:
            self._error(400, str(e))
        except FileNotFoundError as e:
            self._error(404, str(e))

    def _serve_index(self):
        page = TOOL_DIR / "index.html"
        if not page.is_file():
            return self._error(404, "index.html missing")
        body = page.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_audio(self, name, sub):
        if Path(name).name != name:
            return self._error(400, "not a plain filename")
        try:
            path = _inbox_dir(PLAYLISTS, sub) / name
        except ValueError as e:
            return self._error(400, str(e))
        if not path.is_file() or path.suffix.lower() not in AUDIO_EXTS:
            return self._error(404, "audio not found")

        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        rng = self.headers.get("Range", "")
        m = re.match(r"bytes=(\d*)-(\d*)$", rng)
        if m:
            if m.group(1):
                start = int(m.group(1))
            if m.group(2):
                end = min(int(m.group(2)), size - 1)
            if start > end or start >= size:
                return self._error(416, "range not satisfiable")
            status = 206
        length = end - start + 1

        self.send_response(status)
        self.send_header("Content-Type", MIME.get(path.suffix.lower(),
                                                  "application/octet-stream"))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # browser cancelled the stream (seek/close)


def main():
    global MUSIC_ROOT, PLAYLISTS
    ap = argparse.ArgumentParser(description="Drop -> Decide inbox processor")
    ap.add_argument("--music-root",
                    help="folder containing playlists/ (overrides config.json "
                         "and INBOX_MUSIC_ROOT)")
    args = ap.parse_args()
    if args.music_root:
        MUSIC_ROOT = resolve_music_root(args.music_root)
        PLAYLISTS = MUSIC_ROOT / "playlists"

    inbox_dir = PLAYLISTS / "_inbox"
    if not inbox_dir.is_dir():
        print(f"warning: {inbox_dir} does not exist — set music_root in "
              "config.json, INBOX_MUSIC_ROOT, or --music-root", file=sys.stderr)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"Drop -> Decide  |  inbox: {inbox_dir}")
    print(f"Serving on {url}  (Ctrl+C to quit)")
    if not os.environ.get("INBOX_NO_BROWSER"):
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
