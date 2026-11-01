"""Tests for mixtape.py — dates, naming, out-dir resolution, ffmpeg argv, export."""
import subprocess
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import mixtape


# ---------- recorded_date / _stat_date ----------

def test_date_from_obs_filename(tmp_path):
    p = tmp_path / "2026-10-31 16-15-06.mov"
    p.touch()
    assert mixtape.recorded_date(p) == date(2026, 10, 31)


def test_date_anywhere_in_stem(tmp_path):
    p = tmp_path / "set 2025-01-02 final.mp4"
    p.touch()
    assert mixtape.recorded_date(p) == date(2025, 1, 2)


def test_date_invalid_in_filename_falls_back(tmp_path):
    # month 13 is not a real date -> stat fallback (fresh tmp file = today)
    p = tmp_path / "2026-13-45 bogus.mov"
    p.touch()
    assert mixtape.recorded_date(p) == date.today()


def test_date_no_match_falls_back_to_stat(tmp_path):
    p = tmp_path / "random_recording.mov"
    p.touch()
    assert mixtape.recorded_date(p) == date.today()


def test_stat_date_prefers_birthtime():
    st = SimpleNamespace(st_birthtime=datetime(2026, 10, 31, 16, 15).timestamp(),
                         st_mtime=datetime(2020, 1, 1).timestamp())
    assert mixtape._stat_date(st) == date(2026, 10, 31)


def test_stat_date_uses_mtime_without_birthtime():
    st = SimpleNamespace(st_mtime=datetime(2020, 3, 4, 5, 6).timestamp())
    assert mixtape._stat_date(st) == date(2020, 3, 4)


# ---------- date_prefix ----------

@pytest.mark.parametrize("d,exp", [
    (date(2026, 10, 31), "10_31_2026"),
    (date(2026, 1, 5), "1_5_2026"),
])
def test_date_prefix(d, exp):
    assert mixtape.date_prefix(d) == exp


# ---------- sanitize_name ----------

@pytest.mark.parametrize("raw,exp", [
    ("workout_mix", "workout_mix"),
    ("Workout Mix!", "workout_mix"),
    ("  peak   time set ", "peak_time_set"),
    ("D'n'B @ 140", "d_n_b_140"),
    ("!!!", ""),
])
def test_sanitize_name(raw, exp):
    assert mixtape.sanitize_name(raw) == exp


# ---------- next_out_path ----------

def test_out_path_first_free(tmp_path):
    p = mixtape.next_out_path(tmp_path, "10_31_2026", "workout_mix")
    assert p == tmp_path / "10_31_2026_workout_mix_1.wav"


def test_out_path_increments_on_collision(tmp_path):
    (tmp_path / "10_31_2026_workout_mix_1.wav").touch()
    (tmp_path / "10_31_2026_workout_mix_2.wav").touch()
    p = mixtape.next_out_path(tmp_path, "10_31_2026", "workout_mix")
    assert p.name == "10_31_2026_workout_mix_3.wav"


def test_out_path_reserved_counts_as_taken(tmp_path):
    reserved = {tmp_path / "10_31_2026_workout_mix_1.wav"}
    p = mixtape.next_out_path(tmp_path, "10_31_2026", "workout_mix", reserved)
    assert p.name == "10_31_2026_workout_mix_2.wav"


# ---------- resolve_out_dir ----------

def test_out_dir_precedence(tmp_path):
    cfg = {"mixtape_out": str(tmp_path / "from_config")}
    env = {"MIXTAPE_OUT": str(tmp_path / "from_env")}
    cli = str(tmp_path / "from_cli")
    assert mixtape.resolve_out_dir(cli, env, cfg) == (tmp_path / "from_cli").resolve()
    assert mixtape.resolve_out_dir(None, env, cfg) == (tmp_path / "from_env").resolve()
    assert mixtape.resolve_out_dir(None, {}, cfg) == (tmp_path / "from_config").resolve()
    assert mixtape.resolve_out_dir(None, {}, {}) is None


def test_out_dir_expands_tilde():
    assert mixtape.resolve_out_dir("~/x", {}, {}) == Path.home() / "x"


# ---------- load_config ----------

def test_load_config(tmp_path):
    (tmp_path / "config.json").write_text('{"mixtape_out": "/x"}')
    assert mixtape.load_config(tmp_path) == {"mixtape_out": "/x"}
    assert mixtape.load_config(tmp_path / "missing") == {}
    (tmp_path / "config.json").write_text("{bad json")
    assert mixtape.load_config(tmp_path) == {}


# ---------- ffmpeg_cmd ----------

def test_ffmpeg_cmd_exact_flags(tmp_path):
    src, dst = tmp_path / "in.mov", tmp_path / "out.wav"
    cmd = mixtape.ffmpeg_cmd(src, dst)
    assert cmd == ["ffmpeg", "-hide_banner", "-loglevel", "warning",
                   "-i", str(src), "-vn", "-acodec", "pcm_s16le",
                   "-ar", "44100", str(dst)]


def test_ffmpeg_cmd_verbose_shows_full_output(tmp_path):
    cmd = mixtape.ffmpeg_cmd(tmp_path / "in.mov", tmp_path / "out.wav", verbose=True)
    assert "-hide_banner" not in cmd and "-loglevel" not in cmd


# ---------- iter_sources ----------

def test_iter_sources_folder_and_file(tmp_path):
    d = tmp_path / "vids"
    d.mkdir()
    mov_b = d / "b.mov"; mov_b.touch()
    mov_a = d / "a.MOV"; mov_a.touch()
    (d / "notes.txt").touch()
    sub = d / "sub"; sub.mkdir()
    (sub / "nested.mov").touch()
    single = tmp_path / "explicit.mp4"; single.touch()
    got = mixtape.iter_sources([str(d), str(single)])
    assert got == [mov_a, mov_b, single]  # sorted in-folder, non-recursive, filtered


def test_iter_sources_missing_warns(tmp_path, capsys):
    got = mixtape.iter_sources([str(tmp_path / "nope")])
    assert got == []
    assert "nope" in capsys.readouterr().err


def test_iter_sources_dedupes(tmp_path):
    f = tmp_path / "x.mov"; f.touch()
    got = mixtape.iter_sources([str(f), str(tmp_path)])
    assert got == [f]


# ---------- export_one ----------

@pytest.fixture
def fake_ffmpeg(monkeypatch):
    """Pretend ffmpeg ran: write the dst file, record the argv."""
    calls = []
    def run(cmd, check=False):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"RIFF")
        return subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr(mixtape.subprocess, "run", run)
    return calls


def test_export_one_runs_ffmpeg(tmp_path, fake_ffmpeg):
    src = tmp_path / "2026-10-31 16-15-06.mov"; src.touch()
    out_dir = tmp_path / "out"
    dst = mixtape.export_one(src, out_dir, "workout_mix")
    assert dst == out_dir / "10_31_2026_workout_mix_1.wav"
    assert dst.read_bytes() == b"RIFF"
    cmd = fake_ffmpeg[0]
    assert cmd[cmd.index("-i") + 1] == str(src)
    assert cmd[-1] == str(dst)


def test_export_one_creates_out_dir(tmp_path, fake_ffmpeg):
    src = tmp_path / "2026-10-31 16-15-06.mov"; src.touch()
    dst = mixtape.export_one(src, tmp_path / "deep" / "nested", "x")
    assert dst.is_file()


def test_export_one_batch_increments(tmp_path, fake_ffmpeg):
    out_dir = tmp_path / "out"
    srcs = []
    for n in ("2026-10-31 16-15-06.mov", "2026-10-31 17-20-30.mov"):
        p = tmp_path / n; p.touch(); srcs.append(p)
    dsts = [mixtape.export_one(s, out_dir, "workout_mix") for s in srcs]
    assert [d.name for d in dsts] == ["10_31_2026_workout_mix_1.wav",
                                      "10_31_2026_workout_mix_2.wav"]


def test_export_one_dry_run_writes_nothing(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("ffmpeg must not run in dry-run")
    monkeypatch.setattr(mixtape.subprocess, "run", boom)
    src = tmp_path / "2026-10-31 16-15-06.mov"; src.touch()
    dst = mixtape.export_one(src, tmp_path / "out", "workout_mix", dry_run=True)
    assert dst.name == "10_31_2026_workout_mix_1.wav"
    assert not dst.exists()
    assert not (tmp_path / "out").exists()


def test_export_one_dry_run_batch_increments_via_reserved(tmp_path, monkeypatch):
    monkeypatch.setattr(mixtape.subprocess, "run", lambda *a, **k: None)
    src = tmp_path / "2026-10-31 16-15-06.mov"; src.touch()
    reserved, out = set(), tmp_path / "out"
    d1 = mixtape.export_one(src, out, "workout_mix", dry_run=True, reserved=reserved)
    d2 = mixtape.export_one(src, out, "workout_mix", dry_run=True, reserved=reserved)
    assert d1.name.endswith("_1.wav") and d2.name.endswith("_2.wav")


def test_export_one_ffmpeg_failure_removes_partial(tmp_path, monkeypatch):
    def run(cmd, check=False):
        Path(cmd[-1]).write_bytes(b"partial")
        raise subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(mixtape.subprocess, "run", run)
    src = tmp_path / "2026-10-31 16-15-06.mov"; src.touch()
    out_dir = tmp_path / "out"
    dst = mixtape.export_one(src, out_dir, "workout_mix")
    assert dst is None
    assert not (out_dir / "10_31_2026_workout_mix_1.wav").exists()
