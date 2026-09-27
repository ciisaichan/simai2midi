"""score_writer 测试：乐谱构建、网格量化、MusicXML 写出与渲染。

依赖 music21（乐谱构建）。verovio / cairosvg / pypdf 只在渲染测试里用到，
缺失时该测试会打印跳过提示。
"""
import math
import os
import re
import tempfile

import score_writer as sw

try:
    import music21
    HAS_MUSIC21 = True
except ImportError:  # pragma: no cover
    HAS_MUSIC21 = False

BPM120_TICK = 0.5 / sw.TICKS_PER_BEAT  # 120 BPM 下 1 tick 的秒数


def approx(a, b, tol=1e-6):
    assert math.isclose(a, b, abs_tol=tol), f"{a} != {b}"


def _score(hits, bpm_changes=None, **kw):
    return sw.build_score(hits, bpm_changes or [(0.0, 120.0)], **kw)


def _measures(score):
    return list(score.parts[0].getElementsByClass("Measure"))


def _content(measure):
    """取小节的音符序列（music21 可能把内容放进 Voice）。"""
    voices = measure.voices
    return voices[0] if voices else measure


def _measure_quarters(measure):
    return sum(float(el.quarterLength)
               for el in _content(measure).notesAndRests)


def _note_ticks(score):
    return [int(round(float(n.getOffsetInHierarchy(score))
                      * sw.TICKS_PER_BEAT))
            for n in score.recurse().notes]


def _tick_to_seconds(tick):
    return tick * BPM120_TICK


# ---------------------------------------------------------------- 参数解析

def test_parse_time_sig_valid():
    assert sw.parse_time_sig("4/4") == (4, 4)
    assert sw.parse_time_sig("3/8") == (3, 8)
    assert sw.parse_time_sig("6/4") == (6, 4)


def test_parse_time_sig_invalid():
    for bad in ("4", "4/0", "0/4", "a/b", "", "4/-4"):
        try:
            sw.parse_time_sig(bad)
        except ValueError:
            continue
        raise AssertionError(f"应当拒绝 {bad!r}")


def test_grid_from_div():
    assert sw.grid_from_div(4) == 480
    assert sw.grid_from_div(8) == 240
    assert sw.grid_from_div(16) == 120
    assert sw.grid_from_div(32) == 60
    assert sw.grid_from_div(64) == 30
    assert sw.grid_from_div(192) == 10
    assert sw.grid_from_div(0) == 0
    # 192 = 64×3：唯一能同时精确表示 32 分（60tk）与三连 16 分（80tk）的网格
    assert sw.DEFAULT_GRID_DIV == 192, "默认网格应为 192 分（兼容三连音系分工）"
    assert sw.DEFAULT_GRID_TICKS == 10
    try:
        sw.grid_from_div(-8)
    except ValueError:
        pass
    else:
        raise AssertionError("负分母应当报错")


# ---------------------------------------------------------------- 命中收集

def test_collect_hits_sorts_and_dedups():
    """同时命中的多个音符只算一个拍点（记谱上一个符头）。"""
    from simai_parser import Note
    notes = [Note(0.5, "tap", "1"), Note(0.0, "tap", "2"),
             Note(0.5, "tap", "3"), Note(1.25, "hold", "4", duration=0.5)]
    assert sw.collect_hits(notes) == [0.0, 0.5, 1.25]


def test_collect_hits_tails_toggle():
    from simai_parser import Note
    n = Note(0.0, "hold", "1", duration=1.0)
    assert sw.collect_hits([n]) == [0.0]
    assert sw.collect_hits([n], tails=True) == [0.0, 1.0]


# ---------------------------------------------------------------- 乐谱结构

def test_build_score_basic_shape():
    hits = [_tick_to_seconds(t) for t in (0, 480, 960, 1440, 1920)]
    score = _score(hits, title="テスト", subtitle="Lv.13.7 ReMaster",
                   composer="作曲者", designer="譜面: 譜師")
    part = score.parts[0]
    # 乐器名保留语义，靠 MusicXML 的 print-object="no" 不打印（MuseScore 做法）
    assert part.partName == sw.LINE_INSTRUMENT_NAME, part.partName
    assert len(_measures(score)) == 2
    clefs = list(part.recurse().getElementsByClass("Clef"))
    assert any(c.sign == "percussion" for c in clefs), clefs
    ratios = [t.ratioString for t in
              part.recurse().getElementsByClass("TimeSignature")]
    assert ratios and ratios[0] == "4/4", ratios
    marks = list(part.recurse().getElementsByClass("MetronomeMark"))
    assert marks and marks[0].number == 120, marks
    # 主标题=曲名，副标题=难度，曲作者/谱作者各有位置
    assert score.metadata.title == "テスト"
    assert score.metadata.movementName == "Lv.13.7 ReMaster"
    assert score.metadata.composer == "作曲者"
    assert score.metadata.lyricist == "譜面: 譜師"


def test_single_line_staff_and_stems_up():
    """打击乐单线谱：staffLines=1，且符干一律朝上。"""
    score = _score([0.0, 0.5, 1.0])
    part = score.parts[0]
    layouts = list(part.recurse().getElementsByClass("StaffLayout"))
    assert layouts, "缺少 StaffLayout（单线谱）"
    assert int(layouts[0].staffLines) == 1, layouts[0].staffLines
    notes = list(part.recurse().notes)
    assert notes
    for n in notes:
        assert n.stemDirection == "up", f"符干方向错误：{n.stemDirection}"


def test_short_noteheads_with_explicit_rests():
    """每个音符都是短符头，到下一命中之间的空白用显式休止符补齐。"""
    hits = [_tick_to_seconds(t) for t in (0, 960, 1920, 2880)]   # 间隔 2 拍
    score = _score(hits)
    part = score.parts[0]
    notes = list(part.recurse().notes)
    # 默认短符头 = 8 分音符（0.5 拍），不随间隔拉长
    assert [float(n.quarterLength) for n in notes] == [0.5] * 4, \
        [float(n.quarterLength) for n in notes]
    rests = [r for r in part.recurse() if isinstance(r, music21.note.Rest)]
    assert rests, "应当出现显式休止符，而不是把音符拉长"
    total_rest = sum(float(r.quarterLength) for r in rests)
    assert total_rest > 0
    for m in _measures(score):
        approx(_measure_quarters(m), 4.0)


def test_note_div_option_shortens_noteheads():
    """--score-note-div 16 时符头更短，休止符更多。"""
    hits = [_tick_to_seconds(t) for t in (0, 960)]
    score = _score(hits, note_div=16)
    notes = list(score.parts[0].recurse().notes)
    assert [float(n.quarterLength) for n in notes] == [0.25, 0.25], \
        [float(n.quarterLength) for n in notes]


def test_noteheads_use_the_staff_line_display_position():
    """符头必须落在单线谱的线上。

    verovio 把单线画在 E4 的位置（实测 1 个音高 = 90 单位）；music21 的
    unpitched 音符默认显示音位是 B4，会浮在线上方 4 个音高。
    """
    assert (sw.LINE_STEP, sw.LINE_OCTAVE) == ("E", 4)
    score = _score([0.0, 0.5, 1.0, 1.5])
    notes = list(score.parts[0].recurse().notes)
    assert notes
    for n in notes:
        assert n.displayStep == sw.LINE_STEP, n.displayStep
        assert n.displayOctave == sw.LINE_OCTAVE, n.displayOctave


def test_playback_uses_percussion_mapping_not_display_pitch():
    """回归：显示音位只影响记谱，播放必须由打击乐映射决定。

    只写 <unpitched>（不写 <midi-channel>10</midi-channel> 与
    <midi-unpitched>）时，播放器无从得知这是打击乐，会按显示音高 E4
    当旋律乐器弹——听起来又低又没有打击感。
    """
    score = _score([0.0, 0.5, 1.0])
    part = score.parts[0]
    insts = list(part.recurse().getElementsByClass(
        music21.instrument.UnpitchedPercussion))
    assert insts, "缺少无音高打击乐乐器声明"
    inst = insts[0]
    assert inst.midiChannel == sw.DRUM_CHANNEL, inst.midiChannel
    assert inst.percMapPitch == sw.HAND_CLAP, inst.percMapPitch
    # 显示音位仍固定在线上（E4），与播放音色完全解耦
    for n in part.recurse().notes:
        assert (n.displayStep, n.displayOctave) == ("E", 4)


def test_triplet_divisions_become_tuplets():
    """三连音系分工（{12}/{24}/{48}/{192}）必须写成真正的连音时值。

    默认 192 网格（= 64×3）的意义就在这里：二进制网格会把 80 tick 的
    三连 16 分挤成 60 或 90 tick 的二进制时值，节奏就写错了。

    连音整拍成组：``renotate_beats`` 按拍判定，一拍内所有元素（含休止符）的名义
    时值（时值 × 3/2）都是标准记谱值时，整拍写成 3:2 连音组并放 start/stop 括号；
    否则写成二进制时值。music21 的 ``makeTupletBrackets`` 之所以坏，是因为它逐个
    音符贴标记，连音组被非连音元素切断，产出只有一个音的「连音组」，MuseScore 据此
    重算小节长度就报「不完整小节」（实测 PANDORA ReMaster 小节 53/55/57/59/66，
    它读出的 MIDI 只有 66.7% 拍点正确）。
    """
    # 120 BPM 下 1 拍 = 0.5s；三连 16 分 = 1/6 拍
    score = _score([i * 0.5 / 6 for i in range(24)])      # 24 个 = 4 拍
    # 24 个三连 16 分正好填满 1 小节；末尾补 1 拍休止完成收尾，故为 2 小节
    assert len(_measures(score)) == 2
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.musicxml")
        sw.save_musicxml(score, path)
        text = open(path, encoding="utf-8").read()
    assert "<actual-notes>3</actual-notes>" in text, "连音比率应为 3:2"
    assert "<normal-notes>2</normal-notes>" in text
    # 括号必须成对且真的存在：整拍一组的 start / stop。
    # 只写 <time-modification> 不写括号是不行的——MuseScore 会按记谱值自行推导
    # 分组，推不出来就报「不完整小节」（这种退化分组的残留就是 type="stop"
    # 多于 start 或落点错位）。
    assert text.count('type="start"') == text.count('type="stop"'), \
        "连音括号必须成对"
    assert text.count('type="start"') > 0, "三连音拍应写成合法连音组"
    # 反例：32 分音符（60 tick）是二进制时值，不该产生连音
    binary = _score([i * 0.5 / 8 for i in range(32)])
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "b.musicxml")
        sw.save_musicxml(binary, path)
        text = open(path, encoding="utf-8").read()
    assert 'type="start"' not in text, "二进制时值不该产生连音组"


def test_no_ties_and_one_notehead_per_hit():
    """打击乐谱面不该有连结音，也不该出现凭空多出来的假符头。

    music21 的 makeNotation 会把跨拍或时值不规则的音符拆成「连结音 + 中间
    符头」；merge_tied_notes() 负责合回去。实测 PANDORA PARADOXXX ReMaster
    曾被写成 1108 个符头（真实拍点 1095）外加 19 组连结音。
    """
    steps = (80, 40, 40, 240, 80, 160, 40, 200, 120, 80, 320, 40)
    hits = []
    tick = 0
    for step in steps:
        hits.append(tick * BPM120_TICK)
        tick += step
    hits.append(tick * BPM120_TICK)
    score = _score(hits)
    part = score.parts[0]
    notes = [n for n in part.recurse().notes if not n.isRest]
    assert len(notes) == len(hits), f"{len(notes)} 个符头 vs {len(hits)} 个拍点"
    assert not any(n.tie is not None for n in part.recurse().notes), "不应有连结音"

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.musicxml")
        sw.save_musicxml(score, path)
        text = open(path, encoding="utf-8").read()
    assert "<tie" not in text, "XML 里不该有连结音"
    assert text.count("<unpitched>") == len(hits), "符头数与拍点数不一致"
    # 符尾与连音括号必须成对，否则 verovio 会报 left open
    for tag, head, tail in (("beam", "begin", "end"),
                            ("tuplet", "start", "stop")):
        opens = len(re.findall(rf'<{tag}[^>]*>{head}<', text))
        closes = len(re.findall(rf'<{tag}[^>]*>{tail}<', text))
        assert opens == closes, f"{tag} 配对不平：{opens} vs {closes}"


def test_no_complex_durations_left_for_the_exporter():
    """导出器无法表示 2.5 拍这类时值，构造阶段就要拆干净。

    music21 的导出器遇到 complex duration 会直接抛
    Cannot convert complex durations（我们的导出走 makeNotation=False）。
    """
    hits = [t * 37 * BPM120_TICK for t in range(60)]       # 刻意用非整齐间隔
    score = _score(hits, grid_ticks=0)
    for element in score.parts[0].recurse().notesAndRests:
        assert element.duration.type != "complex", \
            f"{element.duration.quarterLength} 拍无法单值记谱"


def test_normalise_musicxml_matches_musescore_structure():
    """单元测试：补齐 music21 不写的结构元素（对照 MuseScore 导出）。"""
    raw = (
        '<score-partwise><part-list><score-part id="P1">'
        '<part-name>Hand Clap</part-name>'
        '<part-abbreviation>Hd. Clp.</part-abbreviation>'
        '<score-instrument id="I1">'
        '<instrument-name>Hand Clap</instrument-name></score-instrument>'
        '<midi-instrument id="I1"><midi-channel>10</midi-channel>'
        '<midi-unpitched>40</midi-unpitched></midi-instrument>'
        '</score-part></part-list>'
        '<part id="P1"><measure number="1">'
        '<note><unpitched><display-step>E</display-step></unpitched>'
        '<duration>120</duration><type>eighth</type></note>'
        '<note><rest /><duration>120</duration><type>eighth</type></note>'
        '</measure></part></score-partwise>')
    out = sw.normalise_musicxml(raw, volume=78.740157)
    assert '<part-name print-object="no">Hand Clap</part-name>' in out
    assert '<part-abbreviation print-object="no">' in out
    # unpitched 音符补 <instrument>，休止符不补；两者都补 <voice>
    assert out.count('<instrument id="I1"/>') == 1, out
    assert out.count("<voice>1</voice>") == 2, out
    # 顺序必须符合 MusicXML：duration → instrument → voice → type
    note = out[out.find("<unpitched"):out.find("</note>")]
    assert (note.index("</duration>") < note.index("<instrument")
            < note.index("<voice>") < note.index("<type>")), note
    # volume / pan 排在 <midi-unpitched> 之后
    assert "</midi-unpitched><volume>78.7402</volume><pan>0</pan>" in out


def test_normalise_musicxml_without_volume_skips_them():
    raw = ('<score-partwise><part-list><score-part id="P">'
           '<part-name /><score-instrument id="I1" /></score-part>'
           '</part-list></score-partwise>')
    out = sw.normalise_musicxml(raw)
    assert "<volume>" not in out and "<pan>" not in out
    assert '<part-name print-object="no" />' in out


def test_music_font_text_becomes_unicode():
    """回归：verovio 用 Leipzig 输出的速度符号在 cairosvg 下是方框，要换掉。"""
    svg = ('<svg><text x="1" y="2"><tspan class="text">'
           '<tspan font-family="Leipzig" font-size="720px">\ueca5</tspan>'
           '</tspan></text></svg>')
    out = sw.patch_music_font_text(svg)
    assert "\u2669" in out, "未替换成 Unicode 四分音符 ♩"
    assert "Leipzig" not in out, "仍引用 cairosvg 取不到的音乐字体"
    assert 'font-size="504px"' in out, "音符符号字号未缩放到与数字协调"


def test_music_font_text_patch_ignores_normal_text():
    svg = '<svg><tspan font-size="405px">187</tspan></svg>'
    assert sw.patch_music_font_text(svg) == svg


def test_music_font_text_patch_keeps_unknown_codepoints():
    """认不出的私用区码位原样保留，不要悄悄丢掉内容。"""
    svg = ('<svg><tspan font-family="Leipzig" font-size="720px">'
           '\ue999</tspan></svg>')
    assert sw.patch_music_font_text(svg) == svg


def test_time_signature_option_is_applied():
    hits = [0.0, 0.5, 1.0]
    score = _score(hits, time_sig=(3, 4))
    ratios = [t.ratioString for t in
              score.parts[0].recurse().getElementsByClass("TimeSignature")]
    assert ratios[0] == "3/4", ratios


def test_measures_are_complete():
    """每个小节的时值必须正好等于拍号长度（不能出现残缺小节）。"""
    hits = [0.0, 0.5, 1.0, 2.4, 3.1, 5.0]
    score = _score(hits)
    measures = _measures(score)
    assert len(measures) >= 3
    for m in measures:
        approx(_measure_quarters(m), 4.0)


def test_leading_rest_when_first_hit_is_late():
    """首个命中若不在小节开头，前面必须补休止符。"""
    hits = [1.0, 1.5, 2.0, 2.5, 3.0]   # 1.0s = 第 2 拍
    score = _score(hits)
    first = list(_content(_measures(score)[0]).notesAndRests)[0]
    assert isinstance(first, music21.note.Rest), first
    approx(float(first.quarterLength), 2.0)
    assert len(_note_ticks(score)) == 5


def test_trailing_rest_completes_last_measure():
    """最后一个音符之后要补休止符，使末小节完整。"""
    hits = [0.0, 0.5]     # 只有 1 拍，必然要补
    score = _score(hits)
    assert len(_measures(score)) == 1
    approx(_measure_quarters(_measures(score)[0]), 4.0)


def test_build_score_accepts_empty_hits():
    score = _score([])
    assert score is not None
    assert _note_ticks(score) == []


def test_bpm_changes_become_metronome_marks():
    """BPM 变化要写成 MetronomeMark，且不在 0 拍重复堆叠。"""
    score = _score([0.0, 0.5, 2.0, 2.5],
                   bpm_changes=[(0.0, 120.0), (1.0, 150.0)])
    marks = list(score.parts[0].recurse().getElementsByClass("MetronomeMark"))
    numbers = [m.number for m in marks]
    assert 120.0 in numbers and 150.0 in numbers, numbers


# ---------------------------------------------------------------- 网格量化

def test_grid_quantization_snaps_offgrid_hits():
    """回归：滑星经过点按路径比例定位，tick 不在音乐网格上（如 199）。

    不量化时会产生 41/480 拍这类无法记谱的时值，music21 只能用 2048 分
    音符连音去凑，最终写出 MusicXML 时抛 MusicXMLExportException。
    """
    hits = [_tick_to_seconds(t) for t in (0, 60, 199, 240)]
    score = _score(hits, grid_ticks=60)
    ticks = _note_ticks(score)
    for tick in ticks:
        assert tick % 60 == 0, f"tick {tick} 不在 60 tick 网格上"
    assert 180 in ticks, ticks        # 199 -> 180
    assert 240 in ticks, ticks


def test_grid_quantization_merges_collisions():
    """吸附后重合的命中要合并成一个符头。"""
    hits = [_tick_to_seconds(t) for t in (0, 119, 121, 240)]
    score = _score(hits, grid_ticks=60)
    ticks = _note_ticks(score)
    # 119/121 都吸附到 120，合成一个
    assert ticks == [0, 120, 240], ticks


def test_finer_grid_preserves_offgrid_detail():
    """更细的网格（64 分音符）应保留 30 tick 的位置。"""
    hits = [_tick_to_seconds(t) for t in (0, 30, 60)]
    score = _score(hits, grid_ticks=sw.grid_from_div(64))
    ticks = _note_ticks(score)
    assert ticks == [0, 30, 60], ticks


# ---------------------------------------------------------------- 渲染链路

def test_musicxml_written_with_percussion_clef():
    score = _score([0.0, 0.5, 1.0, 1.5], title="テスト",
                   subtitle="Lv.13.7 ReMaster", composer="作曲者",
                   designer="譜面: 譜師")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "s.musicxml")
        sw.save_musicxml(score, path, volume=100 / 127 * 100)
        assert os.path.getsize(path) > 500
        text = open(path, encoding="utf-8").read()
        assert "<sign>percussion</sign>" in text, "缺少打击乐谱号"
        assert "<unpitched>" in text, "缺少 unpitched 打击乐音符"
        assert "テスト" in text, "标题未写出"
        assert "<staff-lines>1</staff-lines>" in text, "不是单线谱"
        assert "<display-step>E</display-step>" in text, "符头未定位到单线上"
        # 播放必须由打击乐映射决定，而不是被显示音高带偏
        assert "<midi-channel>10</midi-channel>" in text, "未声明打击乐通道"
        assert "<midi-unpitched>40</midi-unpitched>" in text, \
            "未声明打击乐音色（GM 39 = Hand Clap）"
        assert "<instrument-name>Hand Clap</instrument-name>" in text, \
            "未声明乐器名"
        assert "<midi-unpitched>" in text, "缺 midi-unpitched 会让播放器按显示音高弹"
        # 乐器名要保留语义但用 print-object="no" 隐藏（MuseScore 的做法）
        assert '<part-name print-object="no">' in text, \
            "乐器名应当用 print-object=\"no\" 隐藏，而不是留空"
        # 与 MuseScore 导出对齐的结构元素
        assert "<voice>1</voice>" in text, "缺 <voice>（导入器普遍期望）"
        assert "<instrument id=" in text, "音符缺 <instrument> 引用"
        assert "<midi-program>1</midi-program>" in text, "缺 <midi-program>"
        assert "<volume>" in text and "<pan>" in text, "缺 <volume>/<pan>"
        assert "<key>" in text and "<fifths>0</fifths>" in text, "缺 <key>"
        assert "Lv.13.7 ReMaster" in text, "副标题未写出"
        assert 'type="composer"' in text and "作曲者" in text, "曲作者未写出"
        assert "譜面: 譜師" in text, "谱作者未写出"


# ---------------------------------------------------------------- 标题注入

def test_inject_header_reserves_space_and_places_text():
    svg = ('<svg width="2100px" height="2970px">'
           '<svg class="definition-scale" viewBox="0 0 21000 29700"></svg>'
           '</svg>')
    out = sw.inject_header(svg, title="アンビバレンス",
                           subtitle="Lv.10.7 Expert",
                           composer="すりぃ×相沢", designer="譜面: 隅田川星人")
    assert 'height="3230px"' in out, "页面高度未增加"
    assert 'viewBox="0 -260 2100 3230"' in out, "未预留顶部标题空间"
    for text in ("アンビバレンス", "Lv.10.7 Expert", "すりぃ×相沢",
                 "譜面: 隅田川星人"):
        assert text in out, f"{text} 未注入"
    # 关键回归：必须插在根 <svg> 前，不能落进 definition-scale 那个
    # 10 倍坐标的嵌套 <svg>，否则文字被缩成 1/10 且位置错乱。
    assert out.rfind("<g font-family") > out.find('class="definition-scale"')


def test_inject_header_without_text_still_reserves_uniform_space():
    """无文字时仍预留空间：多页尺寸一致靠的就是每一页都预留。"""
    svg = '<svg width="2100px" height="2970px"></svg>'
    out = sw.inject_header(svg)
    assert 'height="3230px"' in out
    assert 'viewBox="0 -260 2100 3230"' in out
    assert "<text" not in out, "没有文字时不应注入空 text"


def test_inject_header_escapes_xml():
    svg = '<svg width="2100px" height="2970px"></svg>'
    out = sw.inject_header(svg, title="A & B <c>", composer="x")
    assert "A &amp; B &lt;c&gt;" in out


def test_musicxml_can_be_rendered_by_verovio():
    try:
        import verovio  # noqa: F401
    except ImportError:
        print("  (跳过：未安装 verovio)")
        return
    score = _score([0.0, 0.5, 1.0, 1.5, 2.0], title="T")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "s.musicxml")
        sw.save_musicxml(score, path)
        pages = sw.musicxml_to_svg_pages(path)
    assert pages and "<svg" in pages[0]


def test_render_pdf_produces_valid_pdf():
    """走公共 API：render_pdf 内部会自行补齐 libcairo 库路径。"""
    score = _score([0.0, 0.5, 1.0, 1.5, 2.0], title="T")
    with tempfile.TemporaryDirectory() as d:
        xml = os.path.join(d, "s.musicxml")
        pdf = os.path.join(d, "s.pdf")
        sw.save_musicxml(score, xml)
        try:
            sw.render_pdf(xml, pdf)
        except RuntimeError as exc:
            print(f"  (跳过：渲染依赖不可用 - {str(exc).splitlines()[0]})")
            return
        assert os.path.getsize(pdf) > 1000
        with open(pdf, "rb") as fh:
            assert fh.read(5) == b"%PDF-"


def test_render_pdf_with_header_block():
    """带标题块渲染：内容更多时仍能出合法 PDF，且各页尺寸一致。"""
    hits = [_tick_to_seconds(t) for t in range(0, 4800, 120)]
    score = _score(hits, title="アンビバレンス", subtitle="Lv.10.7 Expert",
                   composer="すりぃ×相沢", designer="譜面: 隅田川星人")
    with tempfile.TemporaryDirectory() as d:
        xml = os.path.join(d, "s.musicxml")
        pdf = os.path.join(d, "s.pdf")
        sw.save_musicxml(score, xml)
        try:
            sw.render_pdf(xml, pdf, header={
                "title": "アンビバレンス", "subtitle": "Lv.10.7 Expert",
                "composer": "すりぃ×相沢", "designer": "譜面: 隅田川星人"})
        except RuntimeError as exc:
            print(f"  (跳过：渲染依赖不可用 - {str(exc).splitlines()[0]})")
            return
        assert os.path.getsize(pdf) > 1000
        try:
            from pypdf import PdfReader
        except ImportError:
            return
        sizes = {(round(float(p.mediabox.width), 1),
                  round(float(p.mediabox.height), 1))
                 for p in PdfReader(pdf).pages}
        assert len(sizes) == 1, f"各页尺寸应一致：{sizes}"


def test_written_positions_match_designed_hits():
    """谱面写下的符头位置必须等于设计拍点——听感直接相关。

    makeNotation 会给符头挂上多余的连音比，导出器按「记谱型 × 连音比」写
    ``<duration>``，时值偏短，于是**该小节里它之后的所有音符整体提前**。
    实测 PANDORA ReMaster 小节 38 偏差 80 tick（≈67ms），听感即节奏错位；
    只检查「小节时值完整」是查不出来的（补个 <forward> 就能骗过去）。
    这里的节奏形状照着那一小节复现：三连音组后面接长符头。
    """
    import xml.etree.ElementTree as ET
    ticks = [160, 200, 240, 520, 560, 600, 880, 920, 960, 1240, 1280, 1320]
    hits = [t * BPM120_TICK for t in ticks]
    root = ET.fromstring(sw.musicxml_bytes(_score(hits)).decode("utf-8"))
    divisions = int(root.find(".//divisions").text)
    scale = sw.TICKS_PER_BEAT / divisions
    positions = []
    for measure in root.iter("measure"):
        cursor = (int(measure.get("number")) - 1) * 4 * sw.TICKS_PER_BEAT
        for child in measure:
            node = child.find("duration")
            if node is None:
                continue
            value = int(node.text) * scale
            if child.tag == "note":
                if child.find("rest") is None:
                    positions.append(cursor)
                if child.find("chord") is None:
                    cursor += value
            elif child.tag in ("rest", "forward"):
                cursor += value
            elif child.tag == "backup":
                cursor -= value
    assert positions == ticks, f"符头位置 {positions} != 设计拍点 {ticks}"


if __name__ == "__main__":
    import sys
    if not HAS_MUSIC21:
        print("跳过：未安装 music21（pip install music21 verovio cairosvg pypdf）")
        raise SystemExit(0)
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
