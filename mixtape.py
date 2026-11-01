#!/usr/bin/env python3
"""Export OBS/video recordings to mixtape WAVs.

Wraps:  ffmpeg -i <src> -vn -acodec pcm_s16le -ar 44100 <dst>
Names:  M_D_YYYY_<name>_<n>.wav  (date from the OBS filename, else file times)
Output: --out > MIXTAPE_OUT env > config.json "mixtape_out" > source's folder

Run: python3 mixtape.py "2026-10-31 16-15-06.mov" --name workout_mix
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent

VIDEO_EXTS = {".mov", ".mp4", ".mkv", ".m4v"}

_OBS_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def load_config(tool_dir=TOOL_DIR):
    """Optional config.json next to mixtape.py, e.g. {"mixtape_out": "~/Music/mixtapes"}."""
    cfg = tool_dir / "config.json"
    if not cfg.is_file():
        return {}
    try:
        return json.loads(cfg.read_text())
    except json.JSONDecodeError as e:
        print(f"config.json: ignoring invalid JSON ({e})", file=sys.stderr)
        return {}


def resolve_out_dir(cli=None, env=None, config=None):
    """--out flag > MIXTAPE_OUT env > config.json["mixtape_out"] > None (caller's fallback)."""
    env = os.environ if env is None else env
    config = load_config() if config is None else config
    raw = cli or env.get("MIXTAPE_OUT") or config.get("mixtape_out")
    return Path(raw).expanduser().resolve() if raw else None


def _stat_date(st):
    """Date from a stat result: creation time when the platform has it, else mtime."""
    ts = getattr(st, "st_birthtime", None)
    if ts is None:
        ts = st.st_mtime
    return datetime.fromtimestamp(ts).date()


def recorded_date(path):
    """Recording date: 'YYYY-MM-DD' in the filename (OBS style), else file times."""
    m = _OBS_DATE.search(path.name)
    if m:
        try:
            return date(*map(int, m.groups()))
        except ValueError:  # e.g. "2026-13-45" — not a real date
            pass
    return _stat_date(path.stat())


def date_prefix(d):
    """date -> M_D_YYYY with no zero padding, e.g. 10_31_2026."""
    return f"{d.month}_{d.day}_{d.year}"


def sanitize_name(raw):
    """'Workout Mix!' -> 'workout_mix'; '' if nothing usable."""
    s = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    return re.sub(r"_+", "_", s)


def next_out_path(out_dir, date_str, name, reserved=None):
    """<out_dir>/<date>_<name>_1.wav, bumping the counter until the path is free.

    `reserved` (a set of paths) counts as taken too, so dry-run batches
    still show the increments they would get.
    """
    n = 1
    while True:
        p = out_dir / f"{date_str}_{name}_{n}.wav"
        if not p.exists() and (reserved is None or p not in reserved):
            return p
        n += 1


def ffmpeg_cmd(src, dst, verbose=False):
    """The exact export command; quiet by default, full ffmpeg output if verbose."""
    cmd = ["ffmpeg"]
    if not verbose:
        cmd += ["-hide_banner", "-loglevel", "warning"]
    cmd += ["-i", str(src), "-vn", "-acodec", "pcm_s16le", "-ar", "44100", str(dst)]
    return cmd


def iter_sources(paths):
    """Files and folders -> ordered, deduped list of video files.

    Folders are scanned one level deep, sorted by name; explicit files are
    taken as-is; anything missing is skipped with a warning.
    """
    out, seen = [], set()
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            candidates = sorted(f for f in p.iterdir()
                                if f.is_file() and f.suffix.lower() in VIDEO_EXTS)
        elif p.is_file():
            candidates = [p]
        else:
            print(f"warning: not found, skipping: {raw}", file=sys.stderr)
            continue
        for f in candidates:
            r = f.resolve()
            if r not in seen:
                seen.add(r)
                out.append(f)
    return out


def export_one(src, out_dir, name, dry_run=False, verbose=False, reserved=None):
    """Export one video to <out_dir>/<date>_<name>_<n>.wav. Returns dst or None."""
    dst = next_out_path(out_dir, date_prefix(recorded_date(src)), name, reserved)
    if reserved is not None:
        reserved.add(dst)
    print(f"{src.name} -> {dst.name}")
    if dry_run:
        return dst
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(ffmpeg_cmd(src, dst, verbose), check=True)
    except subprocess.CalledProcessError as e:
        print(f"error: ffmpeg failed for {src} (exit {e.returncode})", file=sys.stderr)
        dst.unlink(missing_ok=True)  # don't leave a partial wav behind
        return None
    return dst


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Export video recordings to mixtape WAVs "
                    "(44.1 kHz 16-bit PCM, video stripped).")
    ap.add_argument("paths", nargs="+",
                    help="video files and/or folders (folders scanned for "
                         + "/".join(sorted(VIDEO_EXTS)) + ")")
    ap.add_argument("-n", "--name", help="mix name (prompted once if omitted)")
    ap.add_argument("--out", help="output folder (overrides MIXTAPE_OUT env "
                                  "and config.json mixtape_out)")
    ap.add_argument("--dry-run", action="store_true",
                    help="show planned outputs without running ffmpeg")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="show full ffmpeg output")
    args = ap.parse_args(argv)

    if not args.dry_run and shutil.which("ffmpeg") is None:
        sys.exit("error: ffmpeg not found on PATH (brew install ffmpeg)")

    sources = iter_sources(args.paths)
    if not sources:
        sys.exit("error: no video files found in the given paths")

    if args.name is not None:
        name = sanitize_name(args.name) or "mix"
    else:
        try:
            raw = input("Mix name [mix]: ").strip()
        except EOFError:
            sys.exit("error: no --name given and stdin is not interactive")
        except KeyboardInterrupt:
            print()
            sys.exit(1)
        name = sanitize_name(raw) or "mix"

    out_dir = resolve_out_dir(args.out)
    if out_dir is None:
        print("note: mixtape_out not set — saving next to each source file "
              "(set it in config.json or MIXTAPE_OUT)", file=sys.stderr)
    else:
        print(f"out: {out_dir}")

    reserved = set()
    done = []
    for src in sources:
        dst = export_one(src, out_dir or src.parent, name,
                         dry_run=args.dry_run, verbose=args.verbose,
                         reserved=reserved)
        if dst is not None:
            done.append(dst)

    if args.dry_run:
        print(f"dry run — {len(done)} file(s) would be written")
        return
    failed = len(sources) - len(done)
    print(f"done — {len(done)} exported" + (f", {failed} failed" if failed else ""))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
