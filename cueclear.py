#!/usr/bin/env python3
"""Selectively clear cue points (hot and memory) from a rekordbox XML export.

Workflow: File > Export Collection in xml format, run this, import the result.
Marks:  Type 0 = cue, Type 4 = loop; Num -1 = memory, Num 0-7 = hot slot A-H.
Output: input copied verbatim minus the removed POSITION_MARK elements —
        the input file is never modified.

Run: python3 cueclear.py export.xml --memory --dry-run
"""
import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

_ATTR = re.compile(r'(\w+)="([^"]*)"')
_TRACK_ID = re.compile(r'TrackID="([^"]*)"')

KIND_LABELS = {"hot_cue": "hot cue", "memory_cue": "memory cue",
               "hot_loop": "hot loop", "memory_loop": "memory loop"}


def classify_mark(attrs):
    """POSITION_MARK attrs -> 'hot_cue' | 'memory_cue' | 'hot_loop' | 'memory_loop'."""
    is_loop = attrs.get("Type") == "4"
    is_hot = attrs.get("Num", "-1") != "-1"
    if is_loop:
        return "hot_loop" if is_hot else "memory_loop"
    return "hot_cue" if is_hot else "memory_cue"


def parse_attrs(text):
    """Attribute dict from an XML element string (may span multiple lines)."""
    return dict(_ATTR.findall(text))


def drop_kinds(hot=False, memory=False, cues=False, loops=False):
    """CLI flags -> set of mark kinds to remove. An omitted axis means 'both'."""
    if not (hot or memory):
        hot = memory = True
    if not (cues or loops):
        cues = loops = True
    kinds = set()
    if hot and cues:
        kinds.add("hot_cue")
    if hot and loops:
        kinds.add("hot_loop")
    if memory and cues:
        kinds.add("memory_cue")
    if memory and loops:
        kinds.add("memory_loop")
    return kinds


def collection_index(root):
    """TrackID -> (Name, Artist, Location) for every track in COLLECTION.

    Playlist <TRACK Key=...> entries carry no TrackID, so they're skipped.
    """
    idx = {}
    for track in root.iter("TRACK"):
        tid = track.get("TrackID")
        if tid is not None:
            idx[tid] = (track.get("Name", ""), track.get("Artist", ""),
                        track.get("Location", ""))
    return idx


def parse_playlists(root):
    """Playlist name -> set of TrackIDs, recursing into folder NODEs.

    Same-named playlists are merged (union).
    """
    playlists = {}

    def walk(node):
        if node.get("Type") == "1":
            keys = {t.get("Key") for t in node.findall("TRACK") if t.get("Key")}
            playlists.setdefault(node.get("Name", ""), set()).update(keys)
        for child in node.findall("NODE"):
            walk(child)

    playlists_node = root.find("PLAYLISTS")
    if playlists_node is not None:
        for node in playlists_node.findall("NODE"):
            walk(node)
    return playlists


def select_tracks(index, playlists, playlist=None, match=None, path_contains=None):
    """TrackIDs to touch: the whole collection, narrowed by each selector (AND)."""
    ids = set(index)
    if playlist is not None:
        wanted = playlist.lower()
        keys = set()
        for name, track_ids in playlists.items():
            if name.lower() == wanted:
                keys |= track_ids
        ids &= keys
    if match is not None:
        m = match.lower()
        ids &= {tid for tid, (name, artist, _loc) in index.items()
                if m in name.lower() or m in artist.lower()}
    if path_contains is not None:
        p = path_contains.lower()
        ids &= {tid for tid, (_n, _a, loc) in index.items() if p in loc.lower()}
    return ids


def filter_lines(lines, drop, targets=None):
    """Copy lines verbatim, dropping POSITION_MARK elements of the given kinds.

    `drop`: set of kinds from drop_kinds(). `targets`: set of TrackIDs to
    touch, or None for the whole collection. Returns (out_lines, stats) where
    stats has per-kind counts and the set of affected TrackIDs.

    POSITION_MARK elements may wrap across lines, so they're collected until
    '/>' and classified as a unit. Track context comes from '<TRACK TrackID='
    lines (rekordbox always emits TrackID first); playlist <TRACK Key=...>
    lines don't match and never change the context.
    """
    out = []
    stats = {kind: 0 for kind in KIND_LABELS}
    stats["tracks"] = set()
    current_track = None
    buf = None  # list of lines while collecting a wrapped POSITION_MARK

    def flush(text):
        attrs = parse_attrs(text)
        kind = classify_mark(attrs)
        if current_track is not None and kind in drop \
                and (targets is None or current_track in targets):
            stats[kind] += 1
            stats["tracks"].add(current_track)
        else:
            out.append(text)

    for line in lines:
        if buf is not None:
            buf.append(line)
            if "/>" in line:
                text, buf = "".join(buf), None
                flush(text)
            continue
        if "<TRACK " in line and 'TrackID="' in line:
            m = _TRACK_ID.search(line)
            current_track = m.group(1) if m else None
            out.append(line)
            continue
        if "<POSITION_MARK" in line:
            if "/>" in line:
                flush(line)
            else:
                buf = [line]
            continue
        out.append(line)
    return out, stats


def summarize(stats):
    """Stats -> '2 hot cues, 1 memory loop' or 'nothing'."""
    parts = [f"{stats[k]} {KIND_LABELS[k]}{'s' if stats[k] != 1 else ''}"
             for k in ("hot_cue", "memory_cue", "hot_loop", "memory_loop")
             if stats[k]]
    return ", ".join(parts) if parts else "nothing"


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Selectively clear cue points (hot and memory) from a "
                    "rekordbox XML export. Writes a copy; never edits the input.")
    ap.add_argument("xml", help="rekordbox XML export "
                                "(File > Export Collection in xml format)")
    ap.add_argument("-o", "--out",
                        help="output path (default: <name>_cleared.xml next to input)")
    ap.add_argument("--hot", action="store_true",
                    help="clear hot cues/loops (slots A-H)")
    ap.add_argument("--memory", action="store_true",
                    help="clear memory cues/loops")
    ap.add_argument("--cues", action="store_true",
                    help="restrict to point cues")
    ap.add_argument("--loops", action="store_true",
                    help="restrict to loops")
    ap.add_argument("--playlist",
                    help="only tracks in this playlist (name, case-insensitive)")
    ap.add_argument("--match",
                    help="only tracks whose title or artist contains this text")
    ap.add_argument("--path-contains",
                    help="only tracks whose file location contains this text")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be cleared, write nothing")
    args = ap.parse_args(argv)

    src = Path(args.xml).expanduser()
    if not src.is_file():
        sys.exit(f"error: not found: {src}")
    dst = (Path(args.out).expanduser() if args.out
           else src.with_name(f"{src.stem}_cleared{src.suffix}"))
    if dst.resolve() == src.resolve():
        sys.exit("error: --out must differ from the input "
                 "(this tool never edits in place)")

    root = ET.parse(src).getroot()
    index = collection_index(root)

    targets = None
    if args.playlist or args.match or args.path_contains:
        playlists = parse_playlists(root)
        if args.playlist and not any(n.lower() == args.playlist.lower()
                                     for n in playlists):
            names = ", ".join(sorted(repr(n) for n in playlists)) or "(none)"
            sys.exit(f"error: playlist not found: {args.playlist!r}\n"
                     f"available: {names}")
        targets = select_tracks(index, playlists, playlist=args.playlist,
                                match=args.match,
                                path_contains=args.path_contains)
        if not targets:
            sys.exit("error: selectors matched 0 tracks — nothing to do")
        print(f"targets: {len(targets)} track(s)")

    drop = drop_kinds(args.hot, args.memory, args.cues, args.loops)
    with src.open(encoding="utf-8", newline="") as f:
        out_lines, stats = filter_lines(f, drop, targets)

    summary = summarize(stats)
    if args.dry_run:
        print(f"dry run — would clear {summary} "
              f"across {len(stats['tracks'])} track(s)")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("w", encoding="utf-8", newline="") as f:
        f.write("".join(out_lines))
    print(f"cleared {summary} across {len(stats['tracks'])} track(s)")
    print(f"wrote: {dst}")


if __name__ == "__main__":
    main()
