"""midi_writer 测试：时间映射与非 latin-1 曲名写出。"""
import math
import os
import tempfile

import mido

import midi_writer as mw
from simai_parser import Note


def approx(a, b, tol=1e-6):
    assert math.isclose(a, b, abs_tol=tol), f"{a} != {b}"


def _onsets(path, charset="utf-8"):
    t, out = 0.0, []
    for msg in mido.MidiFile(path, charset=charset):
        t += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            out.append(t)
    return out


def test_non_ascii_title_roundtrip():
    """日文曲名必须能写出并原样读回。

    mido 的 MidiFile 默认 charset='latin1'，直接 mf.save() 会抛
    UnicodeEncodeError —— 这是实际踩到的 bug，必须由 save_midi 处理。
    """
    title = "アンビバレンス [Master]"
    notes = [Note(0.0, "tap", "1"), Note(0.5, "tap", "2")]
    mf = mw.build_midi(notes, [(0.0, 120.0)], title=title)
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "jp.mid")
        mw.save_midi(mf, path)
        back = mido.MidiFile(path, charset="utf-8")
        names = [m.name for t in back.tracks for m in t
                 if m.type == "track_name"]
        assert title in names, names


def test_save_midi_restores_charset():
    """save_midi 结束后必须恢复原 charset，即使写出失败。"""
    mf = mw.build_midi([Note(0.0, "tap", "1")], [(0.0, 120.0)], title="あ")
    original = mf.charset
    with tempfile.TemporaryDirectory() as d:
        mw.save_midi(mf, os.path.join(d, "ok.mid"))
    assert mf.charset == original
    # 写到不存在的目录 -> 抛错，charset 仍要恢复
    try:
        mw.save_midi(mf, "/nonexistent_dir_xyz/a.mid")
    except OSError:
        pass
    assert mf.charset == original


def test_failed_save_leaves_no_corrupt_file():
    """写出失败不能留下损坏的 .mid。

    实际踩到的 bug：mf.save() 在编码 track_name 时抛错，但 MThd 头
    已写入磁盘，留下 14 字节的坏文件，后续读取报 EOFError。
    这里用无法编码的 charset 强制失败，验证目标路径不被创建。
    """
    mf = mw.build_midi([Note(0.0, "tap", "1")], [(0.0, 120.0)], title="アンビ")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "broken.mid")
        try:
            mw.save_midi(mf, path, charset="latin-1")
        except UnicodeEncodeError:
            pass
        else:
            raise AssertionError("预期 latin-1 编码日文曲名会失败")
        assert not os.path.exists(path), "失败时不应留下目标文件"
        # 临时文件也要清理掉
        leftovers = [f for f in os.listdir(d) if f.endswith(".tmp")]
        assert not leftovers, leftovers


def test_failed_save_keeps_existing_file_intact():
    """覆盖写失败时，原有文件必须保持完好。"""
    good = mw.build_midi([Note(0.0, "tap", "1")], [(0.0, 120.0)], title="ok")
    bad = mw.build_midi([Note(0.0, "tap", "1")], [(0.0, 120.0)], title="アンビ")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "x.mid")
        mw.save_midi(good, path)
        before = open(path, "rb").read()
        try:
            mw.save_midi(bad, path, charset="latin-1")
        except UnicodeEncodeError:
            pass
        assert open(path, "rb").read() == before, "原文件被破坏"


def test_ascii_title_unaffected():
    """ASCII 曲名的输出不受 charset 改动影响。"""
    notes = [Note(0.0, "tap", "1")]
    mf1 = mw.build_midi(notes, [(0.0, 120.0)], title="AMABIE")
    mf2 = mw.build_midi(notes, [(0.0, 120.0)], title="AMABIE")
    with tempfile.TemporaryDirectory() as d:
        p1, p2 = os.path.join(d, "a.mid"), os.path.join(d, "b.mid")
        mw.save_midi(mf1, p1)
        mf2.save(p2)  # 原生 latin-1 路径
        assert open(p1, "rb").read() == open(p2, "rb").read()


def test_all_notes_on_drum_channel_as_clap():
    notes = [Note(0.0, "tap", "1"), Note(0.5, "hold", "2", duration=1.0),
             Note(1.0, "slide", "3", duration=0.5, wait=0.5),
             Note(1.5, "touch", "C"), Note(2.0, "touch_hold", "E1",
                                           duration=0.5)]
    mf = mw.build_midi(notes, [(0.0, 120.0)])
    chans, pitches = set(), set()
    for msg in mf:
        if msg.type in ("note_on", "note_off"):
            chans.add(msg.channel)
            pitches.add(msg.note)
    assert chans == {9}, chans
    assert pitches == {mw.HAND_CLAP}, pitches


def test_tempo_changes_keep_absolute_seconds():
    """BPM 变化后，note 的绝对秒数仍要正确。"""
    notes = [Note(0.0, "tap", "1"), Note(2.0, "tap", "2"),
             Note(3.0, "tap", "3")]
    mf = mw.build_midi(notes, [(0.0, 120.0), (2.0, 240.0)])
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.mid")
        mw.save_midi(mf, path)
        got = _onsets(path)
    assert len(got) == 3, got
    for expect, actual in zip([0.0, 2.0, 3.0], got):
        assert abs(expect - actual) < 0.002, (expect, actual)


def test_hold_and_slide_tail_toggle():
    """默认不含尾判；tails=True 时 HOLD/SLIDE 加尾判音。"""
    notes = [Note(0.0, "hold", "1", duration=1.0),
             Note(2.0, "slide", "3", duration=0.5, wait=0.5)]

    def onsets_of(mf):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.mid")
            mw.save_midi(mf, path)
            return _onsets(path)

    plain = onsets_of(mw.build_midi(notes, [(0.0, 120.0)]))
    assert len(plain) == 2, plain
    for expect, actual in zip([0.0, 2.0], plain):
        assert abs(expect - actual) < 0.002, (expect, actual)

    tails = onsets_of(mw.build_midi(notes, [(0.0, 120.0)], tails=True))
    assert len(tails) == 4, tails
    for expect, actual in zip([0.0, 1.0, 2.0, 3.0], tails):
        assert abs(expect - actual) < 0.002, (expect, actual)


def test_slide_tail_toggle_keeps_intermediates():
    """连结滑星：不包含尾判时保留中间键、只去掉终点。"""
    n = Note(0.0, "slide", "1", duration=0.75, wait=0.5,
             slide_hit_times=[0.0, 0.7, 1.0, 1.25])
    assert mw.note_hit_times(n) == [0.0, 0.7, 1.0]
    assert mw.note_hit_times(n, tails=True) == [0.0, 0.7, 1.0, 1.25]


def test_distinct_uses_multiple_pitches():
    notes = [Note(0.0, "tap", "1"), Note(0.5, "hold", "2", duration=0.5),
             Note(1.0, "slide", "3", duration=0.5, wait=0.5),
             Note(1.5, "touch", "C")]
    mf = mw.build_midi(notes, [(0.0, 120.0)], distinct=True)
    pitches = {m.note for m in mf if m.type == "note_on" and m.velocity > 0}
    assert len(pitches) == 4, pitches


if __name__ == "__main__":
    import sys
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{failures} failure(s)")
    sys.exit(1 if failures else 0)
