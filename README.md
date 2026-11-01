# inbox-tool

**Drop → Decide**: A keyboard-first local tool to sort new DJ tracks into open-format and electronic playlists.

Location: `~/Music/Music/Music/inbox_tool/` (sibling of `playlists/`, so Rekordbox never scans it)

## Stack

- Python stdlib `http.server` (no framework)
- `mutagen` as the only dependency in a venv
- Single `index.html` frontend, vanilla JS
- Total ~450 lines
- Runs with `python inbox.py` → opens `localhost:8791`

## Files

### 1. `inbox.py` — server + API

- `GET /` → `index.html`
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

## Key map (all 15 destinations + review_later)

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
