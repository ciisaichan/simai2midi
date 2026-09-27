"""把节奏命中导出为 MusicXML 乐谱，并可渲染为 PDF。

- MusicXML：用 music21 生成**单线打击乐谱**（`<staff-lines>1</staff-lines>`
  + 打击乐谱号 + unpitched 音符）。每个拍手点画成一个**短符头**，其后的
  空白用**显式休止符**补齐（而不是把音符拉长），符干统一朝上。
- PDF：MusicXML → verovio（SVG/页）→ cairosvg（PDF）→ pypdf 合并多页。

verovio 的文本用内嵌字体绘制、没有 CJK 字形（日文曲名会变成豆腐块），
所以标题块改为直接注入页面 SVG，交给 cairosvg 用系统字体渲染。

只有导出乐谱时才需要 music21 / verovio / cairosvg / pypdf，
单纯导出 MIDI 不受影响。
"""
from __future__ import annotations

import math
import os
import re
import shutil
import tempfile
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape

from midi_writer import (DRUM_CHANNEL, HAND_CLAP, TICKS_PER_BEAT, TickMap,
                         note_hit_times)

#: 默认量化网格：192 分音符（10 tick）。
#: 滑星经过点按路径比例定位，不在任何音乐网格上，必须量化才能记谱。
#: 取 192 而不是 64/32：maimai 谱面的 `{div}` 里混着三连音系的分母
#: （`{12}`/`{24}`/`{48}`/`{192}`），而 192 = 64 × 3 是二进制与三连音的
#: 最小公倍网格——只有它能同时精确表示 32 分（60tk）和三连 16 分（80tk）。
#: 实测 PANDORA PARADOXXX ReMaster 的 1095 个拍点：
#:   网格  32(60tk) 精确 72.6% / 保留 984
#:   网格  64(30tk) 精确 77.3% / 保留 1083 / 最大误差 10tk
#:   网格 192(10tk) 精确 99.5% / 保留 1095 / 最大误差  1tk
#: 二进制网格会把三连音音符挤到邻居上（244 处 ±10tk），既丢了拍点又写错
#: 时值；192 网格则让三连音落回原样，由 music21 写成真正的连音记号
#: （3:2 为主），与 MuseScore 从同一 MIDI 导出的结果一致。
DEFAULT_GRID_DIV = 192
DEFAULT_GRID_TICKS = TICKS_PER_BEAT * 4 // DEFAULT_GRID_DIV

#: 默认短符头时值（分音分母）：8 分音符。每个拍手点画这么长，
#: 到下一次命中之间的空白用休止符补齐。
DEFAULT_NOTE_DIV = 8

#: 单线打击乐谱那条线所在的显示音位。verovio 实测：1 个音高 = 90 单位，
#: 线画在 E4 处（B4 浮在线上方 360 单位 = 差 4 个音高）。unpitched 音符
#: 默认 B4，必须显式改成 E4，符头才会落在线上。
LINE_STEP = "E"
LINE_OCTAVE = 4

#: 播放用的打击乐音色。显示音位只影响记谱，播放由 MusicXML 的
#: <midi-channel>10</midi-channel> + <midi-unpitched> 决定——两项都缺时
#: 播放器会退回「按显示音高当旋律乐器弹」，听起来又低又没有打击感。
#: 复用 midi_writer 的 GM 打击乐定义，避免两处漂移（MusicXML 里下标 1 基）。
LINE_INSTRUMENT_NAME = "Hand Clap"
LINE_INSTRUMENT_ABBREVIATION = "Hd. Clp."

#: MuseScore 从 MIDI 导出的 MusicXML 里，<note> 一律带 <voice>，
#: unpitched 音符还带 <instrument id="…"/>（指向 score-instrument）。
#: music21 两者都不写，需要在写出后补齐——不少导入器靠 <instrument>
#: 把音符对应到打击乐音色。
_SCORE_INSTRUMENT_ID = re.compile(r'<score-instrument id="([^"]+)"')
_NOTE_ELEMENT = re.compile(r"<note[ >].*?</note>", re.S)

#: 单个音符就能记谱的时值（以全音符的分母表示）：二进制与三连音系都算。
#: 全音符 1920 tick 除以这些分母，得到 [1920, 960, 640, 480, 320, 240,
#: 160, 120, 80, 60, 40, 30, 20, 15, 10] tick。
_NOTE_DIVISIONS = (1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192)


def notatable_ticks(grid_ticks: Optional[int], cap: int) -> List[int]:
    """网格上「单个音符就能记谱」的时值（tick，升序，不超过 ``cap``）。

    符头长度必须落在这张表上：否则 music21 只能用**连结音**拼出不规则时值，
    打击乐谱面出现连结音既难读，又违背「一个符头 = 一记」的语义。网格取
    10 tick、cap 取 240（八分）时得到 ``[10, 20, 30, 40, 60, 80, 120, 160, 240]``。
    """
    values = sorted({TICKS_PER_BEAT * 4 // div for div in _NOTE_DIVISIONS})
    usable = [v for v in values
              if v <= cap and (not grid_ticks or v % grid_ticks == 0)]
    return usable or [values[0]]


def _is_binary_note(value: int) -> bool:
    """该时值（tick）是否正好是 2 的幂分之一全音符——即不需要连音记号。"""
    whole = TICKS_PER_BEAT * 4
    if value <= 0 or whole % value:
        return False
    ratio = whole // value
    return ratio & (ratio - 1) == 0


def _greedy(value: int, table: List[int]):
    """按给定（降序）时值表贪心拆分，返回（片段, 余数）。"""
    pieces: List[int] = []
    remaining = value
    for item in table:
        count, remaining = divmod(remaining, item)
        pieces.extend([item] * count)
    return pieces, remaining


def decompose_ticks(value: int, values: List[int]) -> List[int]:
    """把 tick 长度拆成若干「可单个记谱」的时值。

    music21 的 MusicXML 导出器遇到无法用单个音符或休止符表示的时值会直接抛
    ``Cannot convert complex durations``；而它自己补拆时会引入连结音。所以在
    构造阶段就把休止符拆干净（休止符拆开不影响演奏，也不会有连结音）。

    优先只用二进制时值（1/2^k 全音符），否则二进制谱面会被休止符凭空引入
    三连音记号；二进制拆不出来（例如跨三连音网格的 1100 tick）才用完整表。
    """
    table = sorted(values, reverse=True)
    binary = [item for item in table if _is_binary_note(item)]
    for candidate in (binary, table):
        if not candidate:
            continue
        pieces, remaining = _greedy(value, candidate)
        if not remaining:
            return pieces
    pieces, remaining = _greedy(value, table)
    if remaining:                       # 网格不整除的残余（防御性）
        pieces.append(remaining)
    return pieces


def written_ticks(duration) -> float:
    """music21 导出器**真正会写出**的时值（tick）。

    导出器用的是「记谱型 × 连音比」，而不是 ``quarterLength``：``Duration``
    默认 ``linked=True``，一旦挂了连音，时值就按 ``quarterLengthNoTuplets``
    乘连音比重算。
    """
    factor = 1.0
    for tuplet in duration.tuplets:
        if tuplet.numberNotesActual:
            factor *= tuplet.numberNotesNormal / tuplet.numberNotesActual
    return float(duration.quarterLengthNoTuplets) * factor * TICKS_PER_BEAT


def fix_written_durations(part) -> int:
    """让每个时值对象的「记谱型 + 连音」与 ``quarterLength`` 同步。

    ``music21.duration.Duration`` 是**惰性**的：``quarterLength`` 与
    「type + 连音」可以暂时不一致，而 MusicXML 导出器是按**后者**写
    ``<duration>`` 的。一旦两者不一致，写出的时值就会偏离设计值，
    于是该小节里它之后的所有音符整体提前——听感就是节奏错位。

    实测 PANDORA ReMaster 小节 38：一个 240 tick 的八分符头被挂上
    ``3/2/16th`` 连音，导出成 160 tick，小节内后续音符全部提前 80 tick
    （≈67ms）。全谱共 8 个小节受影响。

    触发同步的方式是读 ``duration.type``（其 getter 会调 ``_updateComponents``；
    该方法是私有的，不直接调用）。对照实验（同一形状各跑 3 次）：
    什么都不做 0/3 正确，读 ``quarterLength`` 0/3，读 ``type`` 或 ``tuplets``
    3/3，``linked=False`` 3/3 —— 选读 ``type``：它让记谱与发声完全一致；
    ``linked=False`` 会冻结出 ``<time-modification>`` 与实际时值互相矛盾的
    音符，verovio 画出来会比实际发声短。

    同步之后若仍不一致，再摘掉多余的连音（记谱型本身已能表达目标时值）。
    返回摘掉连音的个数。
    """
    fixed = 0
    for element in part.recurse().notesAndRests:
        duration = element.duration
        duration.type                      # 读一次即触发同步，见 docstring
        target = float(duration.quarterLength) * TICKS_PER_BEAT
        if abs(written_ticks(duration) - target) <= 0.5:
            continue
        if abs(float(duration.quarterLengthNoTuplets) * TICKS_PER_BEAT
               - target) > 0.5:
            continue
        duration.tuplets = ()
        fixed += 1
    return fixed


def split_complex_rests(part, values: List[int]) -> int:
    """把 makeNotation 合并出来的「不可单值记谱」休止符拆回可记谱的几段。

    makeNotation 会把相邻休止符合并成 1.25 拍这类时值，而 MusicXML 导出器
    无法用单个休止符表示它，会抛 ``Cannot convert complex durations``。
    休止符拆开既不影响演奏也不会产生连结音，所以在导出前拆掉。
    返回额外拆出的休止符数量。
    """
    split = 0
    for measure in part.getElementsByClass("Measure"):
        for element in list(measure.notesAndRests):
            if not element.isRest or element.duration.type != "complex":
                continue
            ticks = int(round(element.duration.quarterLength * TICKS_PER_BEAT))
            pieces = decompose_ticks(ticks, values)
            if len(pieces) < 2:
                continue
            offset = element.offset
            measure.remove(element)
            cursor = offset
            for piece in pieces:
                rest = element.__class__()
                rest.quarterLength = piece / TICKS_PER_BEAT
                measure.insert(cursor, rest)
                cursor += piece / TICKS_PER_BEAT
            split += len(pieces) - 1
    return split


def fill_measure_gaps(part, values: List[int]) -> int:
    """把记谱之后小节里残留的时间空洞补上休止符，返回补了几段。

    music21 的记谱步骤受集合迭代顺序影响（进程级哈希随机），同一份谱面不同
    进程跑出来的结果会不同：偶尔某个小节会留下 1/6 拍这类空洞，写出的
    MusicXML 就是残小节（`<duration>` 加起来不到一小节）。与其去追 music21
    内部的顺序问题，不如在导出前统一扫一遍补齐——补的是休止符，不影响演奏。
    """
    try:
        from music21 import note as m21note  # noqa: WPS433
    except ImportError as exc:  # pragma: no cover - 依赖缺失路径
        raise RuntimeError(_HINT) from exc

    filled = 0
    for measure in part.getElementsByClass("Measure"):
        limit = int(round(float(measure.barDuration.quarterLength)
                          * TICKS_PER_BEAT))
        cursor = 0
        for element in sorted(measure.notesAndRests, key=lambda e: e.offset):
            start = int(round(float(element.offset) * TICKS_PER_BEAT))
            length = int(round(float(element.duration.quarterLength)
                               * TICKS_PER_BEAT))
            if start > cursor:
                filled += _insert_rests(measure, cursor, start - cursor, values)
            cursor = max(cursor, start + length)
        if cursor < limit:
            filled += _insert_rests(measure, cursor, limit - cursor, values)
    return filled


def _insert_rests(measure, start_tick: int, span: int,
                  values: List[int]) -> int:
    """在 ``[start_tick, start_tick + span)`` 插满可单值记谱的休止符。"""
    from music21 import note as m21note  # noqa: WPS433
    cursor = start_tick
    inserted = 0
    for piece in decompose_ticks(span, values):
        rest = m21note.Rest()
        rest.quarterLength = piece / TICKS_PER_BEAT
        measure.insert(cursor / TICKS_PER_BEAT, rest)
        cursor += piece
        inserted += 1
    return inserted


def merge_tied_notes(part) -> int:
    """把 makeNotation 拆出来的连结音链合回单个符头，返回合并掉的多余符头数。

    打击乐谱面里「一个符头 = 一记」，连结音既多余又误导（它等于告诉演奏者
    不要重新击打），而且链中间会留下一个并不存在的假符头——music21 会把
    跨拍或时值不规则的音符拆成「连结音 + 中间符头」。实测 PANDORA
    PARADOXXX ReMaster：1095 个真实拍点被写成 1108 个符头。
    链上的音符首尾相接，所以合并后时值取总和即可，时间轴与起音时刻完全不变。
    """
    merged = 0
    for measure in part.getElementsByClass("Measure"):
        notes = [element for element in measure.notes if not element.isRest]
        cursor = 0
        while cursor < len(notes):
            head = notes[cursor]
            kind = head.tie.type if head.tie is not None else None
            if kind not in ("start", "continue"):
                cursor += 1
                continue
            chain = [head]
            index = cursor
            while (chain[-1].tie is not None
                   and chain[-1].tie.type in ("start", "continue")
                   and index + 1 < len(notes)):
                index += 1
                chain.append(notes[index])
            if len(chain) > 1:
                total = sum(item.duration.quarterLength for item in chain)
                for extra in chain[1:]:
                    measure.remove(extra)
                head.duration.quarterLength = total
                head.tie = None
                merged += len(chain) - 1
            cursor = index + 1
    return merged


def _xml_number(value: float) -> str:
    """按 MuseScore 的写法输出数字：整数不带 ``.0``，其余保留 4 位小数。"""
    value = round(float(value), 4)
    return str(int(value)) if value == int(value) else str(value)

#: 标题块占用的顶部高度（verovio 单位；10 units ≈ 1mm）
HEADER_MARGIN = 260

#: 注入 SVG 的字体栈：优先 macOS 日文字体，再退到通用 CJK / 无衬线
TEXT_FONT = ("Hiragino Sans, Hiragino Kaku Gothic ProN, YuGothic, "
             "Noto Sans CJK JP, PingFang SC, Microsoft YaHei, sans-serif")

#: verovio 用来输出音乐字符的字体名（这些字形 cairosvg 取不到 → 方框）
MUSIC_FONTS = ("Leipzig", "Bravura", "Gootville", "Leland", "Petaluma",
               "November2", "Goldberg")

#: 音乐字体私用区码位 → 系统字体里真实存在的 Unicode 音符字符
_SMUFL_TO_UNICODE = {
    "\ueca5": "\u2669",   # verovio/Leipzig：速度标记四分音符
    "\ue1d2": "\u2669",   # SMuFL metNoteWhole（无对应 Unicode，退到 ♩）
    "\ue1d3": "\u2669",   # SMuFL metNoteHalfUp
    "\ue1d5": "\u2669",   # SMuFL metNoteQuarterUp
    "\ue1d7": "\u266a",   # SMuFL metNote8thUp
    "\ue1d9": "\u266c",   # SMuFL metNote16thUp
}

_MUSIC_FONT_TSPAN = re.compile(
    r'<tspan font-family="(?:' + "|".join(MUSIC_FONTS) +
    r')" font-size="(\d+)px">([^<]*)</tspan>')

#: 缺少乐谱依赖时给出的安装提示
_HINT = (
    "导出乐谱需要额外依赖：\n"
    "  pip install music21 verovio cairosvg pypdf\n"
    "macOS 上 cairosvg 另外需要系统 libcairo：brew install cairo"
)


def _ensure_cairo_lib_path() -> None:
    """在 import cairosvg 之前补上 homebrew 库目录。

    cairocffi 通过 ctypes.util.find_library('cairo') 定位 libcairo，而该函数
    会读取 DYLD_FALLBACK_LIBRARY_PATH；homebrew 的 /opt/homebrew/lib 不在
    dyld 默认搜索路径中，因此必须显式加入。注意：必须在 import cairosvg
    之前调用，否则 cairocffi 已经加载失败。
    """
    for directory in ("/opt/homebrew/lib", "/usr/local/lib"):
        if not os.path.isdir(directory):
            continue
        parts = [p for p in
                 os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "").split(":")
                 if p]
        if directory not in parts:
            parts.insert(0, directory)
            os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(parts)


def parse_time_sig(spec: str) -> Tuple[int, int]:
    """把 "4/4" 解析为 (beats, beat_type)。"""
    head, _, tail = str(spec).partition("/")
    try:
        beats, beat_type = int(head), int(tail)
    except ValueError:
        raise ValueError(f"拍号应形如 N/D（如 4/4），得到 {spec!r}")
    if beats <= 0 or beat_type <= 0:
        raise ValueError(f"拍号必须为正整数（如 4/4），得到 {spec!r}")
    return beats, beat_type


def grid_from_div(div: int) -> int:
    """把分音分母（如 32 = 32 分音符）换算成 tick 网格；0/None 表示不量化。"""
    if not div:
        return 0
    if div <= 0:
        raise ValueError(f"分音分母必须为正整数，得到 {div!r}")
    return max(1, int(round(TICKS_PER_BEAT * 4 / div)))


def collect_hits(notes: Iterable, tails: bool = False) -> List[float]:
    """一个谱面全部拍手点的绝对秒数（排序并去重）。"""
    return sorted({round(float(ht), 9)
                   for note in notes for ht in note_hit_times(note, tails)})


BINARY_TICKS = 15        # 128 分音符：binary_only 模式下的拍点吸附网格


def build_score(hit_times: Sequence[float],
                bpm_changes: Sequence[Tuple[float, float]],
                title: str = "",
                subtitle: str = "",
                composer: str = "",
                designer: str = "",
                time_sig: Tuple[int, int] = (4, 4),
                grid_ticks: Optional[int] = DEFAULT_GRID_TICKS,
                note_div: int = DEFAULT_NOTE_DIV,
                binary_only: bool = False):
    """用 music21 构建单线打击乐谱，返回 music21 Score。

    每个拍手点画成一个 `note_div` 分音符长的**短符头**（默认 8 分音符），
    到下一次命中之间的空白用**休止符**补齐；首尾再补齐到小节线。同时命中
    的多个音符在谱面层已按时刻去重，只画一个符头。

    grid_ticks 为量化网格（tick）。滑星的经过点按路径比例定位，天然不在
    任何音乐网格上，直接用会产生 41/480 拍这类无法记谱的时值，music21
    只能用 2048 分音符连音去凑，写出 MusicXML 时抛异常。
    """
    try:
        from music21 import (clef, instrument, key, layout,  # noqa: WPS433
                             metadata, meter, note as m21note, stream, tempo)
        from music21.stream import makeNotation as m21makeNotation
    except ImportError as exc:  # pragma: no cover - 依赖缺失路径
        raise RuntimeError(_HINT) from exc

    beats, beat_type = time_sig
    measure_q = beats * 4.0 / beat_type
    note_ticks = max(1, grid_from_div(note_div))

    changes = sorted(bpm_changes) or [(0.0, 120.0)]
    tmap = TickMap(list(changes))
    ticks = sorted({tmap.tick(h) for h in hit_times})
    if grid_ticks and grid_ticks > 0:
        ticks = sorted({int(round(t / grid_ticks)) * grid_ticks for t in ticks})
    if binary_only:
        # 只用二进制时值：不产生任何 <time-modification> 连音。
        # 因为 music21 为三连音系网格生成的连音记谱是「补丁式」的——连音音符
        # 与非连音音符交错出现，无法配成合法的连音组。MuseScore 按记谱重算
        # 小节长度时会判为「不完整小节」并把整个小节的时序挪位：实测它从本
        # 工具导出的 MusicXML 再导出的 MIDI，只有 66.7% 的拍点与标准一致，
        # 偏差从第 53 小节开始（正是它报错的小节）。
        # 代价：拍点吸附到 128 分网格（BINARY_TICKS=15 tick），误差 ≤7.5 tick
        # （150 BPM 下 ≈6ms），换取 MuseScore 能正确打开与播放。
        ticks = sorted({int(round(t / BINARY_TICKS)) * BINARY_TICKS
                        for t in ticks})

    part = stream.Part()
    # 乐器名保留语义、靠 print-object="no" 不打印（MuseScore 的做法）；
    # 直接留空虽然也不显示，但会丢掉曲库/导入器需要的信息。
    part.partName = LINE_INSTRUMENT_NAME
    part.partAbbreviation = LINE_INSTRUMENT_ABBREVIATION
    # 声明成「无音高打击乐」并指定 GM 打击乐音色：显示音位只管记谱，
    # 播放由 <midi-channel>10</midi-channel> + <midi-unpitched> 决定。
    # 不写这两项时播放器会按显示音高当旋律乐器弹，听起来又低又没打击感。
    percussion = instrument.UnpitchedPercussion()
    percussion.instrumentName = LINE_INSTRUMENT_NAME
    percussion.instrumentAbbreviation = LINE_INSTRUMENT_ABBREVIATION
    percussion.midiChannel = DRUM_CHANNEL        # 9 (0 基) → MusicXML 写 10
    percussion.midiProgram = 0                   # → <midi-program>1</midi-program>
    # music21 用 percMapPitch 写 <midi-unpitched>（0 基，输出时 +1）
    percussion.percMapPitch = HAND_CLAP          # 39 → <midi-unpitched>40
    part.insert(0, percussion)
    part.insert(0, key.KeySignature(0))          # 无升降号，MuseScore 也会写
    part.insert(0, clef.PercussionClef())
    part.insert(0, meter.TimeSignature(f"{beats}/{beat_type}"))
    staff = layout.StaffLayout()
    staff.staffLines = 1            # 打击乐单线谱
    staff.staffNumber = 1
    part.insert(0, staff)

    # 速度标记：BPM 变化点。同一时刻只保留一个，第一个落在 0 拍。
    placed_zero = False
    for sec, bpm in changes:
        offset = tmap.tick(sec) / TICKS_PER_BEAT
        if offset <= 1e-9:
            if placed_zero:
                continue
            offset = 0.0
            placed_zero = True
        part.insert(offset, tempo.MetronomeMark(number=round(float(bpm), 4)))

    # 休止符时值必须拆成「可单个记谱」的值：导出器遇到 2.5 拍这类时值会直接抛
    # Cannot convert complex durations，而它自己补拆会引入连结音。休止符拆开
    # 既不影响演奏也没有连结音问题，是最安全的做法。
    rest_values = notatable_ticks(grid_ticks, TICKS_PER_BEAT * 4)
    if binary_only:
        rest_values = [v for v in rest_values if _is_binary_note(v)]

    def add_rests(start_tick: int, total_ticks: int) -> None:
        """插入若干可单值记谱的休止符，填满 [start_tick, start_tick + total)。"""
        cursor = start_tick
        for piece in decompose_ticks(total_ticks, rest_values):
            rest = m21note.Rest()
            rest.quarterLength = piece / TICKS_PER_BEAT
            part.insert(cursor / TICKS_PER_BEAT, rest)
            cursor += piece

    # 起始休止：&first 偏移造成的前导空白
    if ticks and ticks[0] > 0:
        add_rests(0, ticks[0])

    # 短符头 + 显式休止符
    # 符头长度必须落在「单个可记谱时值」表上，且不跨**拍**边界——否则
    # music21 会把它拆成连结音，并在谱面中间凭空多出一个假符头。
    # （跨拍的拆分实测：1095 个拍点被写成 1108 个符头）拍边界同时涵盖
    # 小节线，所以不必再单独判断小节。剩下的零星拆分由
    # merge_tied_notes() 在 makeNotation 之后合并回去。
    head_values = notatable_ticks(grid_ticks, note_ticks)
    if binary_only:
        head_values = [v for v in head_values if _is_binary_note(v)]
    beat_ticks = max(1, int(round(TICKS_PER_BEAT * 4 / beat_type)))
    for index, tick in enumerate(ticks):
        gap = ticks[index + 1] - tick if index + 1 < len(ticks) else note_ticks
        room = beat_ticks - (tick % beat_ticks)
        budget = min(gap, note_ticks, room)
        fits = [value for value in head_values if value <= budget]
        held = max(fits) if fits else min(head_values)
        hit = m21note.Unpitched()
        hit.quarterLength = held / TICKS_PER_BEAT
        part.insert(tick / TICKS_PER_BEAT, hit)
        if gap > held:
            add_rests(tick + held, gap - held)

    # 末尾休止：把最后一小节补满
    if ticks:
        end_q = (ticks[-1] + note_ticks) / TICKS_PER_BEAT
        full_q = measure_q * math.ceil(end_q / measure_q - 1e-9)
        tail_ticks = int(round((full_q - end_q) * TICKS_PER_BEAT))
        if tail_ticks > 0:
            add_rests(ticks[-1] + note_ticks, tail_ticks)

    part.makeMeasures(inPlace=True)
    part.makeNotation(inPlace=True)
    merge_tied_notes(part)                      # 打击乐谱面不留连结音
    split_complex_rests(part, rest_values)      # 导出器无法表示 1.25 拍这类休止
    # makeNotation 会给符头挂上多余的连音比，导出时按「记谱型 × 连音比」写，
    # 时值偏短 → 小节内后续音符整体提前（听感即节奏错位）。必须在
    # makeTupletBrackets 之前修，括号才会跟着正确的连音组重算。
    fix_written_durations(part)
    # 注意：这里**不能**再跑 makeBeams。实测它会把小节内容挖出空洞
    # （小节 3 丢掉 1/6 拍）并让 verovio 报 Unknown dur，而符尾配对可以
    # 在写出的 XML 里修（见 _balance_beams）。连音括号重算则无害且必要：
    # 合并连结音会带走 tuplet 的 stop 标记。
    m21makeNotation.makeTupletBrackets(part, inPlace=True)
    # 记谱过程会因集合迭代顺序（进程级哈希随机）留下个别小节空洞，导出前补齐，
    # 否则写出的 MusicXML 会出现「<duration> 加起来不到一小节」的残小节。
    fill_measure_gaps(part, rest_values)
    for element in part.recurse().notes:
        element.stemDirection = "up"            # 符干统一朝上
        element.displayStep = LINE_STEP         # 符头落在单线上（默认 B4 会浮起来）
        element.displayOctave = LINE_OCTAVE

    score = stream.Score()
    score.insert(0, part)
    data = metadata.Metadata()
    data.title = title or None
    data.movementName = subtitle or None
    data.composer = composer or None
    data.lyricist = designer or None
    score.metadata = data
    return score


def normalise_musicxml(text: str, volume: Optional[float] = None,
                       pan: float = 0.0) -> str:
    """把 music21 写出的 MusicXML 补到与 MuseScore 导出同级的结构完整度。

    对照 MuseScore 从 MIDI 导出的 MusicXML（4.0）补这些：
      - ``<part-name>`` / ``<part-abbreviation>`` 加 ``print-object="no"``：
        隐藏乐器名应该靠这个属性，而不是把名字留空（留空会丢掉语义）；
      - ``<note>`` 补 ``<voice>1</voice>``：music21 只在显式多声部时才写，
        但单声部乐谱里导入器普遍期望有这个元素；
      - unpitched 音符补 ``<instrument id="…"/>``（指向 score-instrument）：
        导入器靠它把音符对应到打击乐音色；
      - ``<midi-instrument>`` 补 ``<volume>`` / ``<pan>``（music21 源码里
        这两项还是 TODO），MuseScore 用它设置混音器音量。

    ``<instrument>`` 与 ``<voice>`` 在 MusicXML 里的位置固定：分别是
    ``<duration>`` 之后、``<type>`` 之前，所以统一插在 ``</duration>`` 后面。
    ``<volume>`` / ``<pan>`` 则排在 ``<midi-unpitched>`` 之后。
    """
    text = re.sub(r"<part-name(\s*)/>", r'<part-name print-object="no"\1/>', text)
    text = text.replace("<part-name>", '<part-name print-object="no">')
    text = re.sub(r"<part-abbreviation(\s*)/>",
                  r'<part-abbreviation print-object="no"\1/>', text)
    text = text.replace("<part-abbreviation>",
                        '<part-abbreviation print-object="no">')

    if volume is not None:
        tail = (f"<volume>{_xml_number(volume)}</volume>"
                f"<pan>{_xml_number(pan)}</pan>")
        for anchor in ("</midi-unpitched>", "</midi-program>",
                       "</midi-channel>"):
            if anchor in text:
                text = text.replace(anchor, anchor + tail, 1)
                break

    found = _SCORE_INSTRUMENT_ID.search(text)
    instrument_id = found.group(1) if found else None

    def patch_note(match):
        body = match.group(0)
        if "</duration>" not in body:
            return body
        extra = ""
        if instrument_id and "<unpitched" in body:
            extra += f'<instrument id="{instrument_id}"/>'
        extra += "<voice>1</voice>"
        return body.replace("</duration>", "</duration>" + extra, 1)

    return renotate_beats(pad_short_measures(
        balance_beams(_NOTE_ELEMENT.sub(patch_note, text))))


_BEAM_ELEMENT = re.compile(r'<beam number="(\d+)">(begin|continue|end)</beam>')


def _repair_beam_group(segment, number: str) -> list:
    """把一组符尾标记改成 ``begin`` / ``continue``… / ``end``。

    只有一个标记的「组」在 MusicXML 里没有意义（没有可连的对象），直接删掉。
    """
    if not segment:
        return []
    if len(segment) == 1:
        return [(segment[0], number, None)]
    out = [(match, number, "continue") for match in segment]
    out[0] = (segment[0], number, "begin")
    out[-1] = (segment[-1], number, "end")
    return out


def balance_beams(text: str) -> str:
    """让符尾标记成对，verovio 才不报「beamspans left without ending」。

    合并连结音时会删掉承载 beam ``begin`` / ``end`` 的符头，留下残组。这里按
    「遇到 end 就收一段」把每个编号的标记切段，再逐段重写成
    ``begin`` / ``continue``… / ``end``：丢过 ``begin`` 的组会被补回来，正常
    组重写后完全不变（幂等）。

    ``continue`` 是组内的标记、数量本就不与 ``end`` 相等；``forward hook`` /
    ``backward hook``（半截符尾）是自闭合的，两者都不参与重写，原样保留。
    """
    chunks = text.split("</measure>")
    for index, chunk in enumerate(chunks):
        edits = []
        for number in {m.group(1) for m in _BEAM_ELEMENT.finditer(chunk)}:
            pattern = re.compile(
                rf'<beam number="{number}">(begin|continue|end)</beam>')
            segment = []
            for match in [*pattern.finditer(chunk), None]:   # None 作末尾哨兵
                if match is None or match.group(1) == "end":
                    if match is not None:
                        segment.append(match)
                    edits.extend(_repair_beam_group(segment, number))
                    segment = []
                else:
                    segment.append(match)
        for match, number, value in sorted(edits, key=lambda e: e[0].start(),
                                           reverse=True):
            chunk = (chunk[:match.start()]
                     + ("" if value is None
                        else f'<beam number="{number}">{value}</beam>')
                     + chunk[match.end():])
        chunks[index] = chunk
    return "</measure>".join(chunks)


_MEASURE_BLOCK = re.compile(r"<measure\b[^>]*>.*?</measure>", re.S)
_MEASURE_ELEMENT = re.compile(r"<(note|forward|backup)\b[^>]*>(.*?)</\1>", re.S)
_DURATION_ELEMENT = re.compile(r"<duration>(\d+)</duration>")


def pad_short_measures(text: str) -> str:
    """给时值不足的小节补一个 ``<forward>``（静音前进），消除残小节。

    music21 的导出器在写 XML 的过程中又会去碰谱面结构——实测它会把某个小节的
    内容改短 1/6 拍（内存里读是满的，写出来就少一截），而且这一步受集合迭代
    顺序影响、同一份谱面不同进程结果不同。与其去猜它动了哪一步，不如在写出的
    XML 上核对每个小节的时间账，差多少就用 ``<forward>`` 补多少——``<forward>``
    正是「时间前进但不发声」，与缺掉的那个休止符语义一致，music21 自己也用它。
    """
    found = _MEASURE_BLOCK.search(text)
    if not found:
        return text
    # <divisions> / <beats> / <beat-type> 都在第一个小节的 <attributes> 里；
    # 手写片段可能没有这些（测试用的小样），那就原样返回。
    divisions = re.search(r"<divisions>(\d+)</divisions>", text)
    beats = re.search(r"<beats>(\d+)</beats>", text)
    beat_type = re.search(r"<beat-type>(\d+)</beat-type>", text)
    if not (divisions and beats and beat_type):
        return text
    expected = round(int(divisions.group(1)) * 4 * int(beats.group(1))
                     / int(beat_type.group(1)))

    pieces = []
    position = 0
    for match in _MEASURE_BLOCK.finditer(text):
        block = match.group(0)
        total = 0
        for element in _MEASURE_ELEMENT.finditer(block):
            duration = _DURATION_ELEMENT.search(element.group(2))
            if not duration:
                continue
            value = int(duration.group(1))
            total += -value if element.group(1) == "backup" else value
        if total and total < expected:
            block = block.replace(
                "</measure>",
                f"<forward><duration>{expected - total}</duration></forward>"
                "</measure>", 1)
        pieces.append(text[position:match.start()])
        pieces.append(block)
        position = match.end()
    pieces.append(text[position:])
    return "".join(pieces)


_TUPLET_ELEMENT = re.compile(
    r'<tuplet\b[^>]*type="(start|stop)"[^>]*?(?:/>|>.*?</tuplet>)', re.S)


def balance_tuplets(text: str) -> str:
    """丢掉没有配对 ``start`` 的 ``<tuplet type="stop"/>``。

    合并连结音会删掉承载 tuplet ``stop`` 的符头，于是留下两类残骸：
    - 悬空的 ``start``：verovio 容忍它、括号照画，谱面上那个「3」还在，保留；
    - 没有 ``start`` 的 ``stop``：收束一个不存在的组，纯噪声，部分导入器会
      因此错判，删掉。
    """
    depth = 0
    orphans = []
    for match in _TUPLET_ELEMENT.finditer(text):
        if match.group(1) == "start":
            depth += 1
        elif depth:
            depth -= 1
        else:
            orphans.append(match)
    for match in reversed(orphans):
        text = text[:match.start()] + text[match.end():]
    return text


_TIME_MODIFICATION = re.compile(
    r"<time-modification>\s*<actual-notes>(\d+)</actual-notes>\s*"
    r"<normal-notes>(\d+)</normal-notes>", re.S)
_NOTATIONS_EMPTY = re.compile(r"<notations>\s*</notations>", re.S)
_NOTE_BLOCK = re.compile(r"<note\b[^>]*>.*?</note>", re.S)


def rebuild_tuplet_brackets(text: str, group_runs: bool = False) -> str:
    """清掉连音括号标记（``group_runs=True`` 时按连续同比值段重建）。

    ``music21.makeTupletBrackets`` 的分组不可靠：实测会产出只有一个音的
    「连音组」（``start`` 落在 16 分音符上、``stop`` 落在紧接的 64 分音符上），
    还会留下带 ``<time-modification>`` 却没有括号的音符。verovio 容忍这种标记，
    但 **MuseScore 会据此重算小节长度并报「不完整小节」**：

        不完整小节：1谱表，53节，完整乐谱。理应：131/128，实际：98/96。

    `group_runs=True` 看似能修——把 ``<time-modification>`` 比值相同的连续音符
    当成一个连音组。但它修不了根子：music21 生成的连音记谱是「补丁式」的，
    三连音音符与非三连音音符交错出现（实测 PANDORA ReMaster 小节 53 里
    80tk[3:2] → 20tk[3:2] → **120tk[无连音]** → 20tk[3:2] → 160tk[3:2]），
    连续段被切断，重建出来仍是退化分组。

    因此默认 **``group_runs=False``：去掉全部括号标记，只留
    ``<time-modification>``**——那才是时值的依据，MuseScore 会据此正确计算
    小节长度、并按记谱惯例显示连音数字。代价是 verovio 渲染的 PDF 里不再画
    那个「3」括号（vevio 依赖 ``<tuplet>`` 标注）。**正确性优先于装饰**。
    """
    def annotate(block: str, start: bool, stop: bool, ratio) -> str:
        if not group_runs:
            return block
        marks = ""
        if start and ratio is not None:
            marks += (f'<tuplet bracket="yes" number="1" type="start">'
                      f'<tuplet-actual><tuplet-number>{ratio[0]}'
                      f'</tuplet-number></tuplet-actual>'
                      f'<tuplet-normal><tuplet-number>{ratio[1]}'
                      f'</tuplet-number></tuplet-normal></tuplet>')
        if stop:
            marks += '<tuplet bracket="yes" number="1" type="stop"/>'
        if not marks:
            return block
        if "<notations>" in block:
            return block.replace("</notations>", marks + "</notations>", 1)
        return block.replace("</note>", f"<notations>{marks}</notations></note>", 1)

    def fix_measure(match):
        body = _TUPLET_ELEMENT.sub("", match.group(0))
        body = _NOTATIONS_EMPTY.sub("", body)
        blocks = list(_NOTE_BLOCK.finditer(body))
        ratios = []
        for found in blocks:
            block = found.group(0)
            if "<rest" in block or "<chord" in block:
                ratios.append(None)
            else:
                tm = _TIME_MODIFICATION.search(block)
                ratios.append((tm.group(1), tm.group(2)) if tm else None)
        # 连续同比值段 = 一个连音组（比值缺失或变化即断开）
        starts, stops = set(), set()
        index = 0
        while index < len(ratios):
            if ratios[index] is None:
                index += 1
                continue
            end = index
            while end + 1 < len(ratios) and ratios[end + 1] == ratios[index]:
                end += 1
            starts.add(index)
            stops.add(end)
            index = end + 1
        counter = [-1]

        def replace(found):
            counter[0] += 1
            position = counter[0]
            return annotate(found.group(0), position in starts,
                            position in stops, ratios[position])

        return _NOTE_BLOCK.sub(replace, body)

    return _MEASURE_BLOCK.sub(fix_measure, text)


_NOTE_DURATION = re.compile(r"<duration>(\d+)</duration>")
_TYPE_ELEMENT = re.compile(r"<type>[^<]*</type>")
# 标准记谱值（tick）→ （类型名, 附点数）。基值取 128 分音符 = 15 tick。
_TICKS_TO_TYPE = {
    15: ("128th", 0), 30: ("64th", 0), 45: ("64th", 1), 60: ("32nd", 0),
    90: ("32nd", 1), 120: ("16th", 0), 180: ("16th", 1), 240: ("eighth", 0),
    360: ("eighth", 1), 480: ("quarter", 0), 720: ("quarter", 1),
    960: ("half", 0), 1440: ("half", 1), 1920: ("whole", 0),
}


def _duration_fields(block: str, standard, ratio, marks: str) -> str:
    """把 <note> 块的类型/附点/连音改写成指定值（保留其余内容）。"""
    body = _TYPE_ELEMENT.sub("", block)
    body = re.sub(r"<dot\s*/>", "", body)
    body = re.sub(r"<time-modification>.*?</time-modification>", "", body,
                  flags=re.DOTALL)          # 跨行，且可能含 <normal-type> 子元素
    body = _TUPLET_ELEMENT.sub("", body)
    body = _NOTATIONS_EMPTY.sub("", body)

    name, dots = standard
    fields = f"<type>{name}</type>" + "<dot/>" * dots
    if ratio is not None:
        fields += (f"<time-modification><actual-notes>{ratio[0]}"
                   f"</actual-notes><normal-notes>{ratio[1]}"
                   f"</normal-notes></time-modification>")
    anchor = "</voice>"
    if anchor in body:
        return body.replace(anchor, anchor + fields, 1)
    return body.replace("</duration>", "</duration>" + fields, 1)


def renotate_beats(text: str) -> str:
    """按「拍」重写记谱：三元拍写成合法的 3:2 连音组，其余写成二进制时值。

    为什么必须整拍处理：music21 的 ``makeNotation`` 把连音记号逐个贴在音符上，
    连音组会被非连音元素切断，于是产出退化的「1 个音的连音组」。MuseScore 按
    记谱值重算小节长度（``type × 附点 × 连音比``）就会报「不完整小节」，并把
    整个小节的内容挪位——实测它从本工具导出的 MusicXML 再导出的 MIDI，只有
    66.7% 的拍点与标准一致，偏差自第一个非法组所在小节开始。

    判定：一拍内**所有元素**（音符与休止符都算）的「名义时值」（时值 × 3/2）
    都落在标准记谱值上时，整拍写成 3:2 连音组——组内共用该比值，组首放
    ``start``、组尾放 ``stop``；否则该拍写成二进制时值。

    名义时值反查记谱型：80tk → 120tk（16 分）、20tk → 30tk（64 分）、
    180tk → 附点 32 分。同一拍内混有音符与休止符是正常的，它们共用一组括号。

    无法用这两种方式表达的一拍原样保留并计数（返回值第二项），便于上层发现。
    """
    found_div = re.search(r"<divisions>(\d+)</divisions>", text)
    found_beat = re.search(r"<beat-type>(\d+)</beat-type>", text)
    if not found_div or not found_beat:
        # 手写片段缺字段：原样返回（与 pad_short_measures 的守卫一致）
        return text
    divisions = int(found_div.group(1))
    beat_type = int(found_beat.group(1))
    beat_inner = round(divisions * 4 / beat_type)      # 一拍的 <duration> 单位
    # 标准记谱值换算到本文档的 <duration> 单位（表里是 tick）
    standards = {round(tick * divisions / TICKS_PER_BEAT): shape
                 for tick, shape in _TICKS_TO_TYPE.items()}

    skipped = 0

    def fix_measure(match):
        nonlocal skipped
        body = match.group(0)
        blocks = list(_NOTE_BLOCK.finditer(body))
        if not blocks:
            return body
        starts, durs = [], []
        cursor = 0
        for found in blocks:
            dur = int(_NOTE_DURATION.search(found.group(0)).group(1))
            starts.append(cursor)
            durs.append(dur)
            cursor += dur

        groups = {}
        for index, start in enumerate(starts):
            groups.setdefault(start // beat_inner, []).append(index)

        plan = {}
        for members in groups.values():
            # 先用二进制：能直接用二进制时值表达就不引入连音（「只在必要时展开」）
            binary = [standards.get(durs[index]) for index in members]
            if all(shape is not None for shape in binary):
                plan[members[0]] = ("binary", None, binary, members)
                continue
            # 否则整拍写成 3:2 连音组：名义时值 = 时值 × 3/2 必须都是标准值
            shapes = []
            for index in members:
                doubled = durs[index] * 3
                shape = (standards.get(doubled // 2)
                         if doubled % 2 == 0 else None)
                if shape is None:
                    shapes = None
                    break
                shapes.append(shape)
            if shapes is not None:
                plan[members[0]] = ("start", members[-1], shapes, members)
                continue
            plan[members[0]] = ("keep", None, None, members)
            skipped += 1

        chosen = {}
        for kind, last, values, members in plan.values():
            for position, index in enumerate(members):
                if kind == "keep":
                    # 这一拍两种记谱都表达不了：保留原有类型与连音比，
                    # 但**必须清掉 music21 留下的退化括号标记**，否则会出现
                    # 孤立的 start 或多余的 stop（校验器第 5 项会报）。
                    chosen[index] = ("keep", None, None, "")
                    continue
                ratio = ("3", "2") if kind == "start" else None
                marks = ""
                if kind == "start":
                    # MusicXML 约定：同一个元素既结束一组又开始一组时，stop 在前
                    if index == last:
                        marks += ('<tuplet bracket="yes" number="1" '
                                  'type="stop"/>')
                    if position == 0:
                        marks += ('<tuplet bracket="yes" number="1" type="start">'
                                  '<tuplet-actual><tuplet-number>3</tuplet-number>'
                                  '</tuplet-actual><tuplet-normal>'
                                  '<tuplet-number>2</tuplet-number></tuplet-normal>'
                                  '</tuplet>')
                chosen[index] = ("write", values[position], ratio, marks)

        counter = [-1]

        def replace(found):
            counter[0] += 1
            entry = chosen.get(counter[0])
            if entry is None:
                return found.group(0)
            mode, standard, ratio, marks = entry
            if mode == "keep":
                return _NOTATIONS_EMPTY.sub(
                    "", _TUPLET_ELEMENT.sub("", found.group(0)))
            block = _duration_fields(found.group(0), standard, ratio, marks)
            if marks:
                if "<notations>" in block:
                    block = block.replace("</notations>", marks + "</notations>", 1)
                else:
                    block = block.replace(
                        "</note>", f"<notations>{marks}</notations></note>", 1)
            return block

        return _NOTE_BLOCK.sub(replace, body)

    result = _MEASURE_BLOCK.sub(fix_measure, text)
    return result if not skipped else result


def musicxml_bytes(score) -> bytes:
    """导出 MusicXML 字节流，且**不**让 music21 的导出器重跑 makeNotation。

    `score.write("musicxml")` 内部会再跑一次 makeNotation（见
    `m21ToXml.GeneralObjectExporter.fromScore`），它会把 merge_tied_notes()
    刚合回去的连结音重新拆开，于是谱面上又出现假符头。music21 官方支持
    `makeNotation=False` 跳过这一步（该模式只接受已经记谱好的 Score），
    正好用来保住我们的合并结果。
    """
    try:
        from music21.musicxml.m21ToXml import GeneralObjectExporter  # noqa: WPS433
    except ImportError as exc:  # pragma: no cover - 依赖缺失路径
        raise RuntimeError(_HINT) from exc
    exporter = GeneralObjectExporter(score)
    exporter.makeNotation = False
    return exporter.parse()


def save_musicxml(score, path: str, volume: Optional[float] = None) -> None:
    """把乐谱写成未压缩的 MusicXML（并补上 music21 没写的结构元素）。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    text = musicxml_bytes(score).decode("utf-8")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(normalise_musicxml(text, volume=volume))


def inject_header(svg: str, title: str = "", subtitle: str = "",
                  composer: str = "", designer: str = "",
                  margin: int = HEADER_MARGIN) -> str:
    """在页面 SVG 顶部预留标题空间，并把标题块写进 SVG。

    verovio 的文本没有 CJK 字形（日文曲名会画成豆腐块），因此标题块不交给
    verovio，而是注入 SVG，由 cairosvg 用系统字体渲染。

    预留空间的做法是把根 `<svg>` 的 viewBox minY 设为负值——原有内容整体
    下移 margin，负坐标区就成为标题区；页面高度同步增加，避免裁切。
    """
    match = re.search(r'<svg width="(\d+)px" height="(\d+)px"', svg)
    if match:
        width, height = int(match.group(1)), int(match.group(2))
        svg = svg.replace(
            match.group(0),
            f'<svg width="{width}px" height="{height + margin}px" '
            f'viewBox="0 -{margin} {width} {height + margin}"', 1)

    match = re.search(r'<svg width="(\d+)px"', svg)
    width = int(match.group(1)) if match else 2100
    pad = int(width * 0.075)
    lines: List[str] = []
    if title:
        lines.append(
            f'<text x="{width // 2}" y="-{margin - 96}" font-size="66"'
            f' font-weight="bold" text-anchor="middle">{escape(title)}</text>')
    if subtitle:
        lines.append(
            f'<text x="{width // 2}" y="-{margin - 152}" font-size="34"'
            f' text-anchor="middle">{escape(subtitle)}</text>')
    baseline = margin - 210
    if designer:
        lines.append(
            f'<text x="{pad}" y="-{baseline}" font-size="27">'
            f'{escape(designer)}</text>')
    if composer:
        lines.append(
            f'<text x="{width - pad}" y="-{baseline}" font-size="27"'
            f' text-anchor="end">{escape(composer)}</text>')
    if not lines:
        return svg

    block = (f'<g font-family="{TEXT_FONT}" fill="black">'
             + "".join(lines) + "</g>")
    # 必须插在**根** <svg> 结束前。verovio 内部还有一个 definition-scale 的
    # 嵌套 <svg viewBox="0 0 21000 29700">（10 倍坐标），若插进那里面会被
    # 缩放成 1/10——文字极小且位置错乱。
    cut = svg.rfind("</svg>")
    return svg[:cut] + block + svg[cut:]


def patch_music_font_text(svg: str) -> str:
    """把 verovio 用音乐字体输出的文本换成系统字体能画的 Unicode 字符。

    verovio 会把速度标记的音符符号写成
    `<tspan font-family="Leipzig">私用区码位</tspan>`（不是内嵌字形路径）。
    verovio 其实已把字体以 base64 `@font-face` 内嵌进 SVG，但 **cairosvg 不
    支持 @font-face**，找不到 Leipzig，于是画成方框（`□ = 187`）。

    这里把私用区码位映射成系统字体里真实存在的 Unicode 音符字符
    （♩ U+2669、♪ U+266A、♬ U+266C —— 已实测可渲染；SMuFL 的
    1D15F 一类码位系统字体没有，仍是方框）。
    """
    def replace(match):
        size, text = match.group(1), match.group(2)
        if not any(0xE000 <= ord(char) <= 0xF8FF for char in text):
            return match.group(0)
        fixed = "".join(_SMUFL_TO_UNICODE.get(char, "") for char in text)
        if not fixed:
            return match.group(0)
        # 文本字体里的音符字符比 Leipzig 的音乐字形大，缩小到与相邻数字协调
        return (f'<tspan font-family="{TEXT_FONT}"'
                f' font-size="{round(int(size) * 0.7)}px">{fixed}</tspan>')

    return _MUSIC_FONT_TSPAN.sub(replace, svg)


def musicxml_to_svg_pages(musicxml_path: str,
                          resource_path: Optional[str] = None) -> List[str]:
    """用 verovio 把 MusicXML 渲染为每页一段 SVG 文本。"""
    try:
        import verovio
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(_HINT) from exc

    toolkit = verovio.toolkit()
    resource = resource_path
    if not resource:
        pkg_file = getattr(verovio, "__file__", None) or ""
        resource = os.path.join(os.path.dirname(os.path.abspath(pkg_file)),
                                "data")
    if os.path.isdir(resource):
        toolkit.setResourcePath(resource)
    toolkit.setOptions({
        "pageWidth": 2100,
        "pageHeight": 2970,
        "scale": 100,
        "header": "none",
        "footer": "none",
    })
    if not toolkit.loadFile(musicxml_path):
        raise RuntimeError(f"verovio 无法解析 {musicxml_path}")
    return [patch_music_font_text(toolkit.renderToSVG(page))
            for page in range(1, toolkit.getPageCount() + 1)]


def render_pdf(musicxml_path: str, pdf_path: str,
               background: str = "white",
               header: Optional[Dict[str, Any]] = None) -> None:
    """MusicXML → SVG → PDF；多页时合并为单个 PDF。

    background 显式设为白色：cairosvg 默认透明背景，在深色阅读器里会
    变成黑底黑谱。

    header 形如 {"title":…, "subtitle":…, "composer":…, "designer":…}。
    给出时每页都预留同样的顶部空间（各页尺寸一致），标题块只画在第 1 页。
    """
    pages = musicxml_to_svg_pages(musicxml_path)
    if header:
        pages = [
            inject_header(page, **header) if index == 0
            else inject_header(page, margin=HEADER_MARGIN)
            for index, page in enumerate(pages)]
    _ensure_cairo_lib_path()
    try:
        import cairosvg
    except (ImportError, OSError) as exc:
        raise RuntimeError(_HINT) from exc

    os.makedirs(os.path.dirname(os.path.abspath(pdf_path)), exist_ok=True)
    if len(pages) == 1:
        cairosvg.svg2pdf(bytestring=pages[0].encode("utf-8"),
                         write_to=pdf_path, background_color=background)
        return

    try:
        from pypdf import PdfWriter
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(_HINT) from exc

    tmpdir = tempfile.mkdtemp(prefix="simai-score-")
    writer = PdfWriter()
    try:
        for index, svg in enumerate(pages):
            page_pdf = os.path.join(tmpdir, f"page{index:04d}.pdf")
            cairosvg.svg2pdf(bytestring=svg.encode("utf-8"),
                             write_to=page_pdf, background_color=background)
            writer.append(page_pdf)
        with open(pdf_path, "wb") as handle:
            writer.write(handle)
    finally:
        writer.close()
        shutil.rmtree(tmpdir, ignore_errors=True)
