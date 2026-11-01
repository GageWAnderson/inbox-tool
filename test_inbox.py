"""Tests for inbox.py — parser, placement, undo, listing. Fixtures use real library filenames."""
import json

import pytest

import inbox


# ---------- parse_filename ----------

@pytest.mark.parametrize("name,exp", [
    # DJ City dialect: "Artist - Title (Version)" / "Artist - Title - Edit (Dirty)"
    ("Drake & 21 Savage - Circo Loco (Dirty).mp3",
     {"artist": "Drake & 21 Savage", "title": "Circo Loco", "version": "Dirty"}),
    ("Avicii ft Aloe Blacc - Wake Me Up (Clean Extended).mp3",
     {"artist": "Avicii ft Aloe Blacc", "title": "Wake Me Up", "version": "Clean Extended"}),
    ("Coi Leray - Players - DJ Saige Put Your Hands Where My Eyes Could See Edit (Dirty).mp3",
     {"artist": "Coi Leray", "title": "Players",
      "version": "DJ Saige Put Your Hands Where My Eyes Could See Edit (Dirty)"}),
    # Beatport dialect: "Artist_-_Title_(Original_Mix)"
    ("BadKlaat_-_Campers_(Original_Mix).mp3",
     {"artist": "BadKlaat", "title": "Campers", "version": "Original Mix"}),
    ("Alande_-_Take_Me_Home_(Original_Mix).wav",
     {"artist": "Alande", "title": "Take Me Home", "version": "Original Mix"}),
    # feat. group stays in the title; only the trailing version tag is stripped
    ("Anabel_Englund,_Vintage_Culture,_Leftwing___Kody_-_Coming_Home_(feat._Anabel_Englund)_(Extended_Mix).wav",
     {"artist": "Anabel Englund, Vintage Culture, Leftwing Kody",
      "title": "Coming Home (feat. Anabel Englund)", "version": "Extended Mix"}),
    # Freeform: no separator -> whole stem as title, empty artist
    ("alec-benjamin-let-me-down-slowly-tevvez-remix-parallel-universe.mp3",
     {"artist": "", "title": "alec-benjamin-let-me-down-slowly-tevvez-remix-parallel-universe", "version": ""}),
    ("alex_jones_1.mp3",
     {"artist": "", "title": "alex jones 1", "version": ""}),
])
def test_parse_filename(name, exp):
    got = inbox.parse_filename(name)
    assert got["artist"] == exp["artist"]
    assert got["title"] == exp["title"]
    assert got["version"] == exp["version"]


def test_parse_badbunny_plain():
    got = inbox.parse_filename("Bad Bunny - MIA.mp3")
    assert got == {"artist": "Bad Bunny", "title": "MIA", "version": ""}


# ---------- filesystem fixtures ----------

@pytest.fixture
def root(tmp_path):
    """Minimal library tree mirroring the real one."""
    playlists = tmp_path / "playlists"
    (playlists / "_inbox" / "review_later").mkdir(parents=True)
    for rel in inbox.DESTINATIONS.values():
        (playlists / rel[1]).mkdir(parents=True, exist_ok=True)
    return playlists


def add_inbox_file(playlists, name, sub=""):
    d = playlists / "_inbox" / sub
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(b"fake audio")
    return p


# ---------- list_tracks ----------

def test_list_tracks_ignores_non_audio_and_review_dir(root):
    add_inbox_file(root, "a.mp3")
    add_inbox_file(root, "b.wav")
    (root / "_inbox" / ".DS_Store").write_bytes(b"x")
    (root / "_inbox" / "decisions.jsonl").write_text("{}\n")
    (root / "_inbox" / "download.zip").write_bytes(b"x")
    add_inbox_file(root, "hidden.mp3", sub="review_later")

    names = [t["file"] for t in inbox.list_tracks(root)]
    assert names == ["a.mp3", "b.wav"]

    review_names = [t["file"] for t in inbox.list_tracks(root, sub="review_later")]
    assert review_names == ["hidden.mp3"]


# ---------- place_file ----------

def test_place_moves_and_logs(root):
    add_inbox_file(root, "track.mp3")
    res = inbox.place_file(root, "track.mp3", "q")

    assert not (root / "_inbox" / "track.mp3").exists()
    assert (root / "electronic" / "dubstep" / "track.mp3").exists()
    assert res["to"] == "electronic/dubstep"

    lines = (root / "_inbox" / "decisions.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["file"] == "track.mp3"
    assert entry["from"] == "_inbox"
    assert entry["to"] == "electronic/dubstep"
    assert "ts" in entry


def test_place_collision_appends_suffix(root):
    add_inbox_file(root, "track.mp3")
    inbox.place_file(root, "track.mp3", "q")
    add_inbox_file(root, "track.mp3")
    res = inbox.place_file(root, "track.mp3", "q")

    assert (root / "electronic" / "dubstep" / "track.mp3").exists()
    assert (root / "electronic" / "dubstep" / "track-2.mp3").exists()
    assert res["placed_as"] == "track-2.mp3"


def test_place_review_later_stays_under_inbox(root):
    add_inbox_file(root, "maybe.mp3")
    inbox.place_file(root, "maybe.mp3", "v")
    assert (root / "_inbox" / "review_later" / "maybe.mp3").exists()


def test_place_from_review_later_to_final_dest(root):
    add_inbox_file(root, "maybe.mp3", sub="review_later")
    inbox.place_file(root, "maybe.mp3", "w", sub="review_later")
    assert (root / "electronic" / "house" / "maybe.mp3").exists()
    entry = json.loads((root / "_inbox" / "decisions.jsonl").read_text().strip())
    assert entry["from"] == "_inbox/review_later"


def test_place_rejects_bad_input(root):
    add_inbox_file(root, "track.mp3")
    with pytest.raises(ValueError):
        inbox.place_file(root, "track.mp3", "ZZ")          # unknown dest key
    with pytest.raises(ValueError):
        inbox.place_file(root, "../track.mp3", "q")         # traversal
    with pytest.raises(FileNotFoundError):
        inbox.place_file(root, "ghost.mp3", "q")            # not in inbox


# ---------- undo_last ----------

def test_undo_reverses_last_place(root):
    add_inbox_file(root, "a.mp3")
    add_inbox_file(root, "b.mp3")
    inbox.place_file(root, "a.mp3", "q")
    inbox.place_file(root, "b.mp3", "3")

    undone = inbox.undo_last(root)
    assert undone["file"] == "b.mp3"
    assert (root / "_inbox" / "b.mp3").exists()
    assert not (root / "open_format" / "latin" / "b.mp3").exists()

    # log now holds only the first placement
    lines = (root / "_inbox" / "decisions.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["file"] == "a.mp3"


def test_undo_on_empty_log_returns_none(root):
    assert inbox.undo_last(root) is None


def test_undo_restores_to_review_later_subdir(root):
    add_inbox_file(root, "maybe.mp3", sub="review_later")
    inbox.place_file(root, "maybe.mp3", "e", sub="review_later")
    inbox.undo_last(root)
    assert (root / "_inbox" / "review_later" / "maybe.mp3").exists()
