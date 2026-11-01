#!/usr/bin/env python3
"""Drop -> Decide: keyboard-first inbox processor for a DJ library.

Scans playlists/_inbox/, shows track metadata (parsed filename + tags),
and moves each track into its playlist folder with a single keypress.
Run: python inbox.py  ->  http://localhost:8791
"""
import json
import os
import re
import shutil
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
MUSIC_ROOT = Path(os.environ.get("INBOX_MUSIC_ROOT", TOOL_DIR.parent)).resolve()
PLAYLISTS = MUSIC_ROOT / "playlists"
PORT = int(os.environ.get("INBOX_PORT", "8791"))

AUDIO_EXTS = {".mp3", ".wav", ".aif", ".aiff", ".flac", ".m4a"}
MIME = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".aif": "audio/x-aiff",
        ".aiff": "audio/x-aiff", ".flac": "audio/flac", ".m4a": "audio/mp4"}
LOG_NAME = "decisions.jsonl"

# key -> (label, path under playlists/)
DESTINATIONS = {
    "1": ("classics_never_fail", "open_format/classics_never_fail"),
    "2": ("current_pop_hip_hop_rnb", "open_format/current_pop_hip_hop_rnb"),
    "3": ("latin", "open_format/latin"),
    "4": ("80s", "open_format/80s"),
    "5": ("90s", "open_format/90s"),
    "6": ("2000s", "open_format/2000s"),
    "7": ("2010s", "open_format/2010s"),
    "8": ("requests", "open_format/requests"),
    "q": ("dubstep", "electronic/dubstep"),
    "w": ("house", "electronic/house"),
    "e": ("big_room", "electronic/big_room"),
    "r": ("hardstyle", "electronic/hardstyle"),
    "t": ("melodic_dubstep", "electronic/melodic_dubstep"),
    "a": ("warm_up", "electronic/warm_up"),
    "s": ("peak", "electronic/peak"),
    "d": ("closing", "electronic/closing"),
    "z": ("samples", "tools/samples"),
    "x": ("acapellas", "tools/acapellas"),
    "c": ("edits_transitions", "tools/edits_transitions"),
    "v": ("review_later", "_inbox/review_later"),
}

VALID_SUBS = ("", "review_later")


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


def place_file(playlists, name, key, sub=""):
    if key not in DESTINATIONS:
        raise ValueError(f"unknown destination key: {key!r}")
    if Path(name).name != name:
        raise ValueError(f"not a plain filename: {name!r}")
    src_dir = _inbox_dir(playlists, sub)
    src = src_dir / name
    if not src.is_file():
        raise FileNotFoundError(name)

    label, rel = DESTINATIONS[key]
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
            self._json({"destinations": {k: {"label": l, "path": p}
                                         for k, (l, p) in DESTINATIONS.items()}})
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
                                 body.get("dest", ""), body.get("dir", ""))
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
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"Drop -> Decide  |  inbox: {PLAYLISTS / '_inbox'}")
    print(f"Serving on {url}  (Ctrl+C to quit)")
    if not os.environ.get("INBOX_NO_BROWSER"):
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
