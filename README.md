# inbox-tool

**Drop → Decide**: A keyboard-first local tool to sort new DJ tracks into open-format and electronic playlists.

Location: anywhere — the path to your music library is configured (see below), not tied to where the tool lives.

## Configuration

The tool needs the folder that contains `playlists/` ("music root"). Resolved in this order:

1. `--music-root /path/to/Music` CLI flag
2. `INBOX_MUSIC_ROOT` environment variable
3. `config.json` next to `inbox.py` (gitignored): `{"music_root": "/path/to/Music"}`
4. Fallback: the tool's own parent directory (i.e. tool sits next to `playlists/`)

The inbox is always `<music_root>/playlists/_inbox`. `~` is expanded. A startup warning is printed if the inbox doesn't exist.

### Destinations

The key → playlist map is data-driven: add a `"destinations"` object to `config.json` to replace the built-in defaults. Reorganizing the library = editing JSON, not code.

```json
{
  "music_root": "/path/to/Music",
  "destinations": {
    "1": {"label": "classics_never_fail", "path": "open_format/classics_never_fail", "group": "Open Format"},
    "q": {"label": "dubstep", "path": "electronic/dubstep", "group": "Electronic — genre"},
    "v": {"label": "review_later", "path": "_inbox/review_later", "group": ""}
  }
}
```

- **key**: one lowercase character. `u` (undo), `,` (skip), `/` (refresh), and space are reserved.
- **label**: shown in the cheat-sheet.
- **path**: folder under `playlists/` (created on demand). Absolute paths and `..` are rejected.
- **group**: optional cheat-sheet heading; entries with the same group cluster together.
- Object order = display order.

Changes apply without a server restart: press `/` in the UI to reload (both the track list and the key map). Invalid entries are skipped with a stderr warning; if none are valid, the defaults below apply. Undo still works after removing a destination — it reads paths from `decisions.jsonl`, not the config.

## Stack

- Python stdlib `http.server` (no framework)
- `mutagen` as the only dependency in a venv
- Single `index.html` frontend, vanilla JS
- Total ~450 lines
- Runs with `python inbox.py` → opens `localhost:8791`

## Files

### 1. `inbox.py` — server + API

- `GET /` → `index.html`
- `GET /api/config` → destinations from `config.json` (re-read per request, so edits need no restart), falling back to the built-in defaults
- `GET /api/tracks` → scans `playlists/_inbox/`, returns JSON: `{file, artist, title, version, bpm, key, genre, duration, tagged}` — ignores `decisions.jsonl`, `.DS_Store`, zips
- `GET /audio/<file>` → streams with Range support (required for click-to-seek in the browser player)
- `POST /api/place {file, dest}` → moves file (collision → `-2` suffix), appends to `decisions.jsonl`
- `POST /api/undo` → pops last log entry, moves file back
- Filename parser handles all 3 dialects found in the library:
  - `Artist - Title - Remix (Dirty)`
  - `Artist_-_Title_(Original_Mix)`
  - freeform lowercase
- Strips version tags `(Dirty)/(Clean)/(Extended Mix)/(Original_Mix)` into a separate badge
- Untagged WAVs parse from filename; BPM/key show as `—` (no fake data)

### 2. `index.html` — single page, keyboard-first

- Left: remaining-track list
- Center: current track card (title, artist, BPM, key, genre, duration) + `<audio>` player with seek
- Right/bottom: destination cheat-sheet grid
- Header: `14 remaining · 9 placed this session`
- Keys: `space` play/pause, `↑/↓` navigate, `u` undo

### 3. `playlists/_inbox/decisions.jsonl`

One JSON per placement: `{ts, file, from, to}` — permanent audit trail.

### 4. `playlists/_inbox/review_later/`

Created at build.

## Default key map (shipped fallback — override via `config.json` destinations)

```
OPEN FORMAT                    ELECTRONIC
1 classics_never_fail          q dubstep        a warm_up
2 current_pop_hip_hop_rnb      w house          s peak
3 latin                        e big_room       d closing
4 80s   5 90s                  r hardstyle
6 2000s 7 2010s                t melodic_dubstep
8 requests

TOOLS                          z samples   x acapellas   c edits_transitions
v review_later · , skip · u undo
```

## Build order (TDD for the risky parts)

1. Tests first for: filename parser (fixtures from real filenames — all 3 dialects), move+log round-trip, undo, collision naming
2. Server + endpoints until tests pass
3. Frontend, then a live smoke test: copy 3 real files into `_inbox`, place them, verify log + undo
4. Run script `run.sh` (activates venv, starts server, opens browser)

## Explicitly not in v1

- BPM/key analysis of untagged WAVs (essentia hook later)
- Waveform rendering
- Rekordbox sync
- Stats page
- Folder watching (manual refresh is one keypress)

---

# mixtape.py — OBS recording → mixtape WAV

Separate standalone CLI (no server, stdlib-only, needs `ffmpeg` on PATH). Wraps:

```
ffmpeg -i <src> -vn -acodec pcm_s16le -ar 44100 <dst>
```

## Usage

```bash
python3 mixtape.py "2026-10-31 16-15-06.mov"              # prompts: Mix name [mix]:
python3 mixtape.py "2026-10-31 16-15-06.mov" -n workout_mix
python3 mixtape.py *.mov -n workout_mix                   # batch -> _1, _2, _3...
python3 mixtape.py ~/Movies/OBS/ -n workout_mix           # folder -> all videos inside
python3 mixtape.py ~/Movies/OBS/ -n workout_mix --dry-run # show plan, convert nothing
```

Output names: `M_D_YYYY_<name>_<n>.wav`, e.g. `10_31_2026_workout_mix_1.wav`.

- **Date**: parsed from `YYYY-MM-DD` in the filename (OBS default), else the file's creation date. No zero-padding.
- **Name**: `--name`/`-n`, prompted once per run if omitted; sanitized to lowercase snake (`Workout Mix!` → `workout_mix`).
- **Counter**: `_1`, `_2`… bumps until free, so re-runs and batches never overwrite.

Output folder, resolved in this order (same pattern as music root):

1. `--out /path` CLI flag
2. `MIXTAPE_OUT` environment variable
3. `config.json` next to `mixtape.py`: `{"mixtape_out": "/path/to/mixtapes"}`
4. Fallback: the source file's own folder (prints a note)

Folders are scanned one level deep for `.mov/.mp4/.mkv/.m4v`; explicit file args are taken as-is. Quiet ffmpeg output by default (`-hide_banner -loglevel warning`); `-v/--verbose` shows everything. A failed export deletes its partial WAV. Handy alias: `alias mixtape='python3 ~/Documents/development/inbox_tool/mixtape.py'`.

Tests: `tests/test_mixtape.py` covers date parsing, name sanitizing, the counter, out-dir resolution, the ffmpeg argv, and export (with ffmpeg mocked).
