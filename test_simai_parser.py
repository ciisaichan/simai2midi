"""simai 解析器单元测试（用例取自 simai 官方文档 pages/1002.html）。"""
import math

import simai_parser as sp


def notes(body, first=0.0, bpm=120.0):
    return sp.parse_chart(body, first=first, default_bpm=bpm)


def approx(a, b, tol=1e-6):
    assert math.isclose(a, b, abs_tol=tol), f"{a} != {b}"


def test_comma_is_one_second_with_explicit_step():
    # {#0.35} -> 逗号 0.35 秒
    r = notes("{#0.35}1,1,1,E")
    approx(r.notes[0].time, 0.0)
    approx(r.notes[1].time, 0.35)
    approx(r.notes[2].time, 0.70)


def test_first_offset_and_bpm174_16th():
    # 文档: BPM174 的 16 分音符 = 0.08620689655...s
    r = notes("(174){16}1,1,1,E", first=1.234)
    approx(r.notes[0].time, 1.234)
    approx(r.notes[1].time, 1.234 + 240 / (174 * 16))
    approx(240 / (174 * 16), 0.0862068965517, 1e-9)


def test_end_marker_extends_by_one_cell():
    # first=1.234, 10 个逗号 1 秒 -> 最后一格结束 11.234
    r = notes("{#1.0}" + "1," * 10 + "E", first=1.234)
    assert len(r.notes) == 10
    approx(r.notes[-1].time, 10.234)
    approx(r.end_time, 11.234)


def test_tap_break_ex_star():
    r = notes("(120){4}1b,2x,3$,4bx,E")
    assert [n.kind for n in r.notes] == ["tap"] * 4
    assert r.notes[0].is_break and not r.notes[0].is_ex
    assert r.notes[1].is_ex and not r.notes[1].is_break
    assert r.notes[3].is_break and r.notes[3].is_ex
    assert not r.warnings, r.warnings


def test_hold_length_forms():
    # 5h[2:1] @BPM174 = 0.689655...s
    r = notes("(174)5h[2:1],E")
    assert r.notes[0].kind == "hold"
    approx(r.notes[0].duration, 240 / (174 * 2))
    # 4h[#5.678] 精确秒
    r = notes("(174)4h[#5.678],E")
    approx(r.notes[0].duration, 5.678)
    # 4h[150#2:1] 指定 BPM
    r = notes("(174)4h[150#2:1],E")
    approx(r.notes[0].duration, 240 / (150 * 2))
    # 伪 TAP HOLD: 无长度 -> [1280:1]
    r = notes("(174)3h,E")
    approx(r.notes[0].duration, 240 / (174 * 1280))
    # BREAK HOLD 两种写法等价
    a = notes("(174)5hb[2:1],E").notes[0]
    b = notes("(174)5bh[2:1],E").notes[0]
    assert a.is_break and b.is_break
    approx(a.duration, b.duration)


def test_slide_basic_wait_is_one_beat():
    r = notes("(120)1-4[8:3],E")
    n = r.notes[0]
    assert n.kind == "slide" and n.position == "1" and n.end_position == "4"
    assert n.slide_shape == "-"
    approx(n.wait, 60 / 120)               # 星星等待 = 1 拍
    approx(n.duration, 240 * 3 / (120 * 8))


def test_slide_length_variants():
    # [160#8:3] 用 BPM160 计算移动时间
    n = notes("(120)1-4[160#8:3],E").notes[0]
    approx(n.duration, 240 * 3 / (160 * 8))
    # [160#2] BPM160 的等待 + 2 秒移动
    n = notes("(120)1-4[160#2],E").notes[0]
    approx(n.duration, 2.0)
    # [3##1.5] 3 秒等待 + 1.5 秒移动
    n = notes("(120)1-4[3##1.5],E").notes[0]
    approx(n.wait, 3.0)
    approx(n.duration, 1.5)
    # [3##8:3] 3 秒等待 + 当前 BPM 8 分 3 个
    n = notes("(120)1-4[3##8:3],E").notes[0]
    approx(n.wait, 3.0)
    approx(n.duration, 240 * 3 / (120 * 8))
    # [3##160#8:3]
    n = notes("(120)1-4[3##160#8:3],E").notes[0]
    approx(n.wait, 3.0)
    approx(n.duration, 240 * 3 / (160 * 8))


def test_same_start_slides_are_two_notes():
    r = notes("(120)1-4[4:3]*-6[8:5],E")
    assert len(r.notes) == 2
    assert all(n.kind == "slide" and n.position == "1" for n in r.notes)
    ends = {n.end_position for n in r.notes}
    assert ends == {"4", "6"}
    approx(r.notes[0].time, r.notes[1].time)
    assert not r.warnings, r.warnings


def test_chained_slide_single_note_summed_duration():
    # 1-4q7-2[1:2] 连结 SLIDE = 1 个 note
    r = notes("(120)1-4q7-2[1:2],E")
    assert len(r.notes) == 1
    n = r.notes[0]
    assert n.slide_shape == "-q-" and n.end_position == "2"
    approx(n.duration, 240 * 2 / (120 * 1))
    # 逐段指定速度：时长相加
    r2 = notes("(120)1-4[2:1]q7[2:1]-2[1:1],E")
    assert len(r2.notes) == 1
    approx(r2.notes[0].duration,
           240 / (120 * 2) + 240 / (120 * 2) + 240 / (120 * 1))
    assert not r.warnings and not r2.warnings


def test_big_v_slide():
    n = notes("(120)1V35[8:3],E").notes[0]
    assert n.slide_shape == "V" and n.end_position == "5"
    assert n.position == "1"


def test_slide_hit_nodes_and_times():
    """普通滑星：头 + 尾两个拍手点。"""
    n = notes("(120)1-4[8:3],E").notes[0]
    assert n.slide_nodes == ["1", "4"]
    assert len(n.slide_hit_times) == 2
    approx(n.slide_hit_times[0], 0.0)
    approx(n.slide_hit_times[1], n.wait + n.duration)


def test_chained_slide_hit_nodes_include_junctions():
    """连结滑星：头 + 每个中间键 + 尾。"""
    n = notes("(120)1-4q7-2[1:2],E").notes[0]
    assert n.slide_nodes == ["1", "4", "7", "2"]


def test_big_v_slide_hit_nodes_include_via():
    n = notes("(120)1V35[8:3],E").notes[0]
    assert n.slide_nodes == ["1", "3", "5"]


def test_dense_arc_expands_intermediate_buttons():
    """dense=True 时外圈弧线展开沿途每个按钮。"""
    n = sp.parse_chart("(120)1<4[8:3],E", dense=True).notes[0]
    assert n.slide_nodes == ["1", "8", "7", "6", "5", "4"]
    n2 = notes("(120)1<4[8:3],E").notes[0]
    assert n2.slide_nodes == ["1", "4"]


def test_arc_hat_is_short_way():
    """^ 取短弧：1 到 4 应走 1-2-3-4。"""
    n = sp.parse_chart("(120)1^4[8:3],E", dense=True).notes[0]
    assert n.slide_nodes == ["1", "2", "3", "4"]


def test_resample_32_to_16():
    """一串 32 分音符，32/16 重采样后每隔一个保留（8→4）。"""
    r = notes("(120){32}" + "1," * 8 + "E")
    assert len(r.notes) == 8
    merged = sp.resample_dense_notes(r.notes, 32, 16, r.bpm_changes)
    assert len(merged) == 4
    # 保留索引 0、2、4、6 的音
    assert merged[0].time == r.notes[0].time
    assert merged[1].time == r.notes[2].time
    assert merged[2].time == r.notes[4].time
    assert merged[3].time == r.notes[6].time


def test_resample_64_to_16():
    """16 个 64 分音符，64/16 重采样后为 4 个 16 分音符。"""
    r = notes("(120){64}" + "1," * 16 + "E")
    assert len(r.notes) == 16
    merged = sp.resample_dense_notes(r.notes, 64, 16, r.bpm_changes)
    assert len(merged) == 4


def test_resample_leaves_normal_notes():
    """4 分音符间隔，32/16 不处理。"""
    r = notes("(120){4}" + "1," * 4 + "E")
    merged = sp.resample_dense_notes(r.notes, 32, 16, r.bpm_changes)
    assert len(merged) == 4


def test_resample_16th_notes_untouched():
    """16 分音符本身不密集，32/16 不改动。"""
    r = notes("(120){16}" + "1," * 8 + "E")
    merged = sp.resample_dense_notes(r.notes, 32, 16, r.bpm_changes)
    assert len(merged) == 8


def test_resample_resets_after_gap():
    """两个密集段被长间隔隔开，各自重采样。"""
    body = "(120){32}" + "1," * 8 + "{4}," + "{32}" + "2," * 8 + "E"
    r = notes(body)
    merged = sp.resample_dense_notes(r.notes, 32, 16, r.bpm_changes)
    assert len(merged) == 8  # 两段各 8→4
    assert merged[0].position == "1" and merged[4].position == "2"


def test_resample_invalid_returns_copy():
    """source/target 无效时原样返回等长新列表。"""
    r = notes("(120){32}" + "1," * 8 + "E")
    assert len(sp.resample_dense_notes(r.notes, 0, 16, r.bpm_changes)) == 8
    assert len(sp.resample_dense_notes(r.notes, 16, 32, r.bpm_changes)) == 8
    assert len(sp.resample_dense_notes(r.notes, 32, 32, r.bpm_changes)) == 8


def test_touch_and_touch_hold():
    r = notes("(120)B1,D4,C,E1h[4:3],Chf[1:2],B7f,E")
    kinds = [(n.kind, n.position) for n in r.notes]
    assert ("touch", "B1") in kinds
    assert ("touch", "D4") in kinds
    assert ("touch", "C") in kinds
    assert ("touch", "B7") in kinds
    th = [n for n in r.notes if n.kind == "touch_hold"]
    assert {n.position for n in th} == {"E1", "C"}
    assert not r.warnings, r.warnings


def test_touch_c1_c2_normalized_to_c():
    r = notes("(120)C1,C2,E")
    assert [n.position for n in r.notes] == ["C", "C"]


def test_each_slash_and_compact():
    r = notes("(120)1/8h[2:1],E")
    assert len(r.notes) == 2
    approx(r.notes[0].time, r.notes[1].time)
    r2 = notes("(120)12,E")  # compact EACH = 两个 TAP
    assert [n.position for n in r2.notes] == ["1", "2"]
    approx(r2.notes[0].time, r2.notes[1].time)


def test_pseudo_each_backtick_offsets_1ms():
    r = notes("(120)1`2`3/4,E")
    by_pos = {n.position: n.time for n in r.notes}
    approx(by_pos["1"], 0.0)
    approx(by_pos["2"], 0.001)
    approx(by_pos["3"], 0.002)
    approx(by_pos["4"], 0.002)


def test_star_suppression_markers_do_not_break_parse():
    for token in ("1?-5[2:1]", "1!-5[2:1]", "1@-5[8:1]"):
        r = notes(f"(120){token},E")
        assert len(r.notes) == 1, (token, r.notes)
        assert r.notes[0].kind == "slide", token
        assert not r.warnings, (token, r.warnings)


def test_bpm_change_midway():
    r = notes("(120){4}1,1,(240){4}1,1,E")
    approx(r.notes[1].time, 0.5)
    approx(r.notes[2].time, 1.0)
    approx(r.notes[3].time, 1.0 + 0.25)
    assert len(r.bpm_changes) == 2


def test_whitespace_and_comments_skipped():
    body = "(120){4}\n  1 ,\t2,\n||注释行\n3,E"
    r = notes(sp.strip_comments(body))
    assert [n.position for n in r.notes] == ["1", "2", "3"]
    assert not r.warnings, r.warnings


def test_maidata_key_parsing_with_continuation():
    text = ("&title=Test\n&first=1.5\n&lv_4=14\n"
            "&inote_4=(120){4}1,2,\n3,4,\nE\n&des_4=someone\n")
    data = sp.parse_maidata(text)
    assert data["title"] == "Test"
    assert data["first"] == "1.5"
    assert "3,4," in data["inote_4"]
    r = notes(data["inote_4"], first=float(data["first"]))
    assert [n.position for n in r.notes] == ["1", "2", "3", "4"]
    approx(r.notes[0].time, 1.5)


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
