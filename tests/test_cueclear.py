"""Tests for cueclear.py — mark classification, selection, streaming filter, CLI."""
import xml.etree.ElementTree as ET

import pytest

import cueclear


FIXTURE = """\
<?xml version="1.0" encoding="UTF-8"?>

<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="7.2.19" Company="AlphaTheta"/>
  <COLLECTION Entries="3">
    <TRACK TrackID="111" Name="Alpha &amp; Omega" Artist="DJ Test" Composer=""
           Album="" Grouping="" Genre="" Kind="WAV File" Size="100" TotalTime="163"
           Location="file://localhost/Music/dubstep/a.wav"
           Remixer="" Tonality="1A" Label="" Mix="">
      <TEMPO Inizio="0.475" Bpm="126.00" Metro="4/4" Battito="2"/>
      <POSITION_MARK Name="" Type="0" Start="0.475" Num="0" Red="255" Green="55" Blue="111"/>
      <POSITION_MARK Name="" Type="0" Start="38.094" Num="3" Red="170" Green="114"
                     Blue="255"/>
      <POSITION_MARK Name="" Type="0" Start="0.475" Num="-1"/>
      <POSITION_MARK Name="" Type="4" Start="144.761" End="152.381" Num="-1"/>
    </TRACK>
    <TRACK TrackID="222" Name="Beta" Artist="Someone" Composer="" Album=""
           Grouping="" Genre="" Kind="WAV File" Size="100" TotalTime="389"
           Location="file://localhost/Music/house/b.wav"
           Remixer="" Tonality="" Label="" Mix="">
      <POSITION_MARK Name="" Type="4" Start="40.021" End="46.421" Num="6" Red="255"
                     Green="180" Blue="0"/>
    </TRACK>
    <TRACK TrackID="333" Name="Gamma" Artist="DJ Test" Composer="" Album=""
           Grouping="" Genre="" Kind="WAV File" Size="100" TotalTime="294"
           Location="file://localhost/Music/dubstep/c.wav"
           Remixer="" Tonality="" Label="" Mix=""/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="Folder" Type="0" Count="1">
        <NODE Name="Heavy Dubstep" Type="1" KeyType="0" Entries="2">
          <TRACK Key="111"/>
          <TRACK Key="333"/>
        </NODE>
      </NODE>
      <NODE Name="House" Type="1" KeyType="0" Entries="1">
        <TRACK Key="222"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""

ALL_KINDS = {"hot_cue", "memory_cue", "hot_loop", "memory_loop"}


def fixture_root():
    return ET.fromstring(FIXTURE)


# ---------- classify_mark ----------

@pytest.mark.parametrize("attrs,exp", [
    ({"Type": "0", "Num": "-1"}, "memory_cue"),
    ({"Type": "0", "Num": "0"}, "hot_cue"),
    ({"Type": "0", "Num": "7"}, "hot_cue"),
    ({"Type": "4", "Num": "-1"}, "memory_loop"),
    ({"Type": "4", "Num": "6"}, "hot_loop"),
])
def test_classify_mark(attrs, exp):
    assert cueclear.classify_mark(attrs) == exp


# ---------- parse_attrs ----------

def test_parse_attrs_single_line():
    text = '<POSITION_MARK Name="" Type="0" Start="0.475" Num="0" Red="255" Green="55" Blue="111"/>'
    attrs = cueclear.parse_attrs(text)
    assert attrs == {"Name": "", "Type": "0", "Start": "0.475", "Num": "0",
                     "Red": "255", "Green": "55", "Blue": "111"}


def test_parse_attrs_multiline_joined():
    text = ('      <POSITION_MARK Name="" Type="4" Start="40.021" End="46.421" Num="6" Red="255"\n'
            '                     Green="180" Blue="0"/>\n')
    attrs = cueclear.parse_attrs(text)
    assert attrs["Type"] == "4" and attrs["End"] == "46.421" and attrs["Blue"] == "0"


# ---------- drop_kinds ----------

@pytest.mark.parametrize("hot,memory,cues,loops,exp", [
    (False, False, False, False, ALL_KINDS),
    (True, False, False, False, {"hot_cue", "hot_loop"}),
    (False, True, False, False, {"memory_cue", "memory_loop"}),
    (False, False, True, False, {"hot_cue", "memory_cue"}),
    (False, False, False, True, {"hot_loop", "memory_loop"}),
    (True, False, True, False, {"hot_cue"}),
    (False, True, False, True, {"memory_loop"}),
    (True, True, True, False, {"hot_cue", "memory_cue"}),
])
def test_drop_kinds(hot, memory, cues, loops, exp):
    assert cueclear.drop_kinds(hot, memory, cues, loops) == exp


# ---------- collection_index / parse_playlists ----------

def test_collection_index():
    idx = cueclear.collection_index(fixture_root())
    assert set(idx) == {"111", "222", "333"}
    assert idx["111"] == ("Alpha & Omega", "DJ Test", "file://localhost/Music/dubstep/a.wav")
    assert idx["222"][0] == "Beta"


def test_parse_playlists_recurses_folders():
    pls = cueclear.parse_playlists(fixture_root())
    assert pls["Heavy Dubstep"] == {"111", "333"}
    assert pls["House"] == {"222"}


# ---------- select_tracks ----------

def test_select_no_selectors_returns_all():
    idx = cueclear.collection_index(fixture_root())
    sel = cueclear.select_tracks(idx, {})
    assert sel == {"111", "222", "333"}


def test_select_by_playlist_case_insensitive():
    idx = cueclear.collection_index(fixture_root())
    pls = cueclear.parse_playlists(fixture_root())
    sel = cueclear.select_tracks(idx, pls, playlist="heavy dubstep")
    assert sel == {"111", "333"}


def test_select_by_match_hits_name_or_artist():
    idx = cueclear.collection_index(fixture_root())
    assert cueclear.select_tracks(idx, {}, match="dj test") == {"111", "333"}
    assert cueclear.select_tracks(idx, {}, match="beta") == {"222"}


def test_select_by_path_contains():
    idx = cueclear.collection_index(fixture_root())
    sel = cueclear.select_tracks(idx, {}, path_contains="DUBSTEP")
    assert sel == {"111", "333"}


def test_selectors_narrow_each_other():
    idx = cueclear.collection_index(fixture_root())
    pls = cueclear.parse_playlists(fixture_root())
    sel = cueclear.select_tracks(idx, pls, playlist="Heavy Dubstep", match="gamma")
    assert sel == {"333"}


# ---------- filter_lines ----------

def run_filter(text, drop, targets=None):
    return cueclear.filter_lines(text.splitlines(keepends=True), drop, targets)


def test_filter_empty_drop_is_byte_identical():
    out, stats = run_filter(FIXTURE, set())
    assert "".join(out) == FIXTURE
    assert sum(stats[k] for k in ALL_KINDS) == 0
    assert stats["tracks"] == set()


def test_filter_clears_memory_marks_keeps_hot():
    out, stats = run_filter(FIXTURE, {"memory_cue", "memory_loop"})
    text = "".join(out)
    # the two memory marks on track 111 are gone
    assert 'Start="144.761"' not in text
    assert text.count('Num="-1"') == 0
    # both hot cues remain, including the one sharing Start with a dropped memory cue
    assert text.count('Start="0.475" Num="0"') == 1
    assert 'Start="38.094" Num="3"' in text
    # hot loop on track 222 untouched
    assert 'Start="40.021"' in text
    assert stats == {"hot_cue": 0, "memory_cue": 1, "hot_loop": 0, "memory_loop": 1,
                     "tracks": {"111"}}


def test_filter_drops_wrapped_mark_as_a_unit():
    out, stats = run_filter(FIXTURE, {"hot_loop"})
    text = "".join(out)
    assert "<POSITION_MARK" not in text.split("</TRACK>")[1]  # track 222 has none left
    assert 'Green="180"' not in text  # no orphan continuation line
    assert stats["hot_loop"] == 1 and stats["tracks"] == {"222"}


def test_filter_respects_targets():
    out, stats = run_filter(FIXTURE, ALL_KINDS, targets={"222"})
    text = "".join(out)
    assert 'Start="0.475" Num="0"' in text  # track 111 untouched
    assert 'Start="40.021"' not in text     # track 222 cleared
    assert stats["tracks"] == {"222"}
    assert sum(stats[k] for k in ALL_KINDS) == 1


def test_filter_playlist_track_key_lines_untouched():
    out, _ = run_filter(FIXTURE, ALL_KINDS, targets={"111", "222", "333"})
    text = "".join(out)
    assert '<TRACK Key="111"/>' in text  # playlist section is not collection TRACK


# ---------- main / end-to-end ----------

def run_main(tmp_path, *argv, text=FIXTURE):
    src = tmp_path / "export.xml"
    src.write_text(text, encoding="utf-8")
    cueclear.main([str(src), *argv])
    return src


def test_main_writes_cleared_copy_never_touches_input(tmp_path):
    src = run_main(tmp_path, "--memory")
    dst = tmp_path / "export_cleared.xml"
    assert src.read_text(encoding="utf-8") == FIXTURE  # input untouched
    out = dst.read_text(encoding="utf-8")
    assert 'Num="-1"' not in out
    assert 'Start="0.475" Num="0"' in out
    ET.fromstring(out)  # output is well-formed XML


def test_main_custom_out(tmp_path):
    src = run_main(tmp_path, "-o", str(tmp_path / "sub" / "out.xml"), "--hot")
    dst = tmp_path / "sub" / "out.xml"
    assert dst.is_file()
    assert 'Num="0"' not in dst.read_text(encoding="utf-8")


def test_main_dry_run_writes_nothing(tmp_path, capsys):
    run_main(tmp_path, "--memory", "--dry-run")
    out = capsys.readouterr().out
    assert "dry run" in out
    assert "1 memory cue" in out and "1 memory loop" in out
    assert not (tmp_path / "export_cleared.xml").exists()


def test_main_refuses_out_equal_to_input(tmp_path):
    src = tmp_path / "export.xml"
    src.write_text(FIXTURE, encoding="utf-8")
    with pytest.raises(SystemExit):
        cueclear.main([str(src), "-o", str(src)])


def test_main_unknown_playlist_lists_available(tmp_path, capsys):
    src = tmp_path / "export.xml"
    src.write_text(FIXTURE, encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        cueclear.main([str(src), "--playlist", "Nope"])
    assert "Heavy Dubstep" in capsys.readouterr().err + str(e.value)


def test_main_selectors_matching_zero_tracks_aborts(tmp_path):
    src = tmp_path / "export.xml"
    src.write_text(FIXTURE, encoding="utf-8")
    with pytest.raises(SystemExit):
        cueclear.main([str(src), "--match", "no such text anywhere"])
    assert not (tmp_path / "export_cleared.xml").exists()


def test_main_idempotent_rerun_clears_nothing(tmp_path, capsys):
    run_main(tmp_path, "--memory")
    capsys.readouterr()
    cleared = (tmp_path / "export_cleared.xml").read_text(encoding="utf-8")
    cueclear.main([str(tmp_path / "export_cleared.xml"), "--memory"])
    out = capsys.readouterr().out
    assert "nothing" in out
    assert (tmp_path / "export_cleared_cleared.xml").read_text(encoding="utf-8") == cleared
