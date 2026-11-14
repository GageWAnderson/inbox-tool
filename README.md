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

Folders are scanned one level deep for `.mov/.mp4/.mkv/.m4v`; explicit file args are taken as-is; a bare filename that isn't found is also looked up inside each folder arg (`mixtape.py ~/Movies clip.mov -n x`). Quiet ffmpeg output by default (`-hide_banner -loglevel warning`); `-v/--verbose` shows everything. A failed export deletes its partial WAV. Handy alias: `alias mixtape='python3 ~/Documents/development/inbox_tool/mixtape.py'`.

Tests: `tests/test_mixtape.py` covers date parsing, name sanitizing, the counter, out-dir resolution, the ffmpeg argv, and export (with ffmpeg mocked).

---

# cueclear.py — selectively clear cues in a rekordbox XML export

Standalone CLI (stdlib-only, no deps). Fills the rekordbox gap of no bulk cue deletion: export your collection, clear hot and/or memory cues (and loops) on all or some tracks, import the result.

## Workflow

1. In rekordbox: **File → Export Collection in xml format**
2. `python3 cueclear.py export.xml --memory` (dry-run first with `--dry-run`)
3. In rekordbox: import the written `export_cleared.xml` — the input file is never modified

## Usage

```bash
python3 cueclear.py export.xml --memory                          # all memory cues + loops, collection-wide
python3 cueclear.py export.xml --hot --cues --playlist "Heavy Dubstep"
python3 cueclear.py export.xml --match "hawkins" --path-contains dubstep
python3 cueclear.py export.xml --memory --dry-run               # report only, write nothing
python3 cueclear.py export.xml --memory -o /path/out.xml
```

**What** (two axes; an omitted axis means "both", no flags at all = all four kinds):

- `--hot` / `--memory` — slot axis: hot cues/loops (slots A–H) vs memory cues/loops
- `--cues` / `--loops` — shape axis: point cues vs loops

**Which tracks** (multiple selectors narrow each other; none = whole collection; matching 0 tracks aborts):

- `--playlist NAME` — case-insensitive playlist name, resolved via the `<PLAYLISTS>` tree (unknown name → error listing available playlists)
- `--match TEXT` — case-insensitive substring on title or artist
- `--path-contains TEXT` — case-insensitive substring on the track's file location

Output defaults to `<name>_cleared.xml` next to the input; `-o` overrides (`--out` equal to the input is refused).

## How it works

- A `<POSITION_MARK>` is classified by `Type` (0 = cue, 4 = loop) and `Num` (−1 = memory, 0–7 = hot slot).
- Quirk: rekordbox often exports a hot cue *twice* — once colored (`Num ≥ 0`) and once as a memory-cue duplicate at the same position. Clearing `--memory` therefore never touches hot cues.
- Two-pass: `ElementTree` reads track/playlist metadata; a streaming line filter then copies the file verbatim, dropping only matching `POSITION_MARK` elements (which may wrap across lines). The diff between input and output contains nothing but removed marks.

Tests: `tests/test_cueclear.py` covers classification, flag→kind mapping, playlist/selector resolution, the wrapped-element and duplicate-mark cases, byte-identical passthrough, dry-run, idempotence, and the CLI safety guards.
