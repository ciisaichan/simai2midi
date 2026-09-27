"""simai (maimai 谱面记谱) 解析器。

把 maidata.txt / inote 正文解析为带绝对秒数的 Note 事件列表。
参考: https://w.atwiki.jp/simai/pages/1002.html
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import slide_geometry

TOUCH_AREAS = "ABCDE"
PSEUDO_EACH_STEP = 0.001  # ` 记法: 每级延后 1ms
PSEUDO_TAP_HOLD_DIV = 1280  # 无长度的 h 视为 [1280:1]


@dataclass
class Note:
    time: float          # 绝对秒（已含 first 偏移）
    kind: str            # tap / hold / slide / touch / touch_hold
    position: str        # "1".."8" 或 "C" / "B3" / "E7"
    duration: float = 0.0    # hold 持续时间；slide 为星星滑行时间
    wait: float = 0.0        # slide 星星等待时间（从 time 起算）
    is_break: bool = False
    is_ex: bool = False
    slide_shape: str = ""
    end_position: str = ""
    slide_nodes: List[str] = field(default_factory=list)   # 滑星命中键（头+中间+尾）
    slide_hit_times: List[float] = field(default_factory=list)  # 对应每个键的绝对秒
    raw: str = ""

    @property
    def end_time(self) -> float:
        if self.kind == "slide":
            return self.time + self.wait + self.duration
        return self.time + self.duration


@dataclass
class ParseResult:
    notes: List[Note] = field(default_factory=list)
    bpm_changes: List[Tuple[float, float]] = field(default_factory=list)
    end_time: float = 0.0
    warnings: List[str] = field(default_factory=list)


SHAPE_CHARS = set("-<>^vpqszVw")
MODIFIER_CHARS = set("bx$@?!f")


def _beat(bpm: float) -> float:
    return 60.0 / bpm


def _note_len(bpm: float, div: float, count: float) -> float:
    """div 分音符 count 个的秒数。"""
    return 240.0 * count / (bpm * div)


def _parse_ratio(spec: str, bpm: float) -> float:
    """解析 'x:y' 或 '#sec' 或 'bpm#x:y' 或 'bpm#sec'。"""
    spec = spec.strip()
    if spec.startswith("#"):
        return float(spec[1:])
    if "#" in spec:
        head, _, tail = spec.partition("#")
        local_bpm = float(head)
        if ":" in tail:
            div, _, cnt = tail.partition(":")
            return _note_len(local_bpm, float(div), float(cnt))
        return float(tail)
    if ":" in spec:
        div, _, cnt = spec.partition(":")
        return _note_len(bpm, float(div), float(cnt))
    return float(spec)


def parse_length(spec: str, bpm: float, is_slide: bool) -> Tuple[float, float]:
    """返回 (wait, duration)。spec 是方括号内的内容。"""
    default_wait = _beat(bpm) if is_slide else 0.0
    if "##" in spec:
        head, _, tail = spec.partition("##")
        return float(head), _parse_ratio(tail, bpm)
    return default_wait, _parse_ratio(spec, bpm)


def strip_comments(body: str) -> str:
    """移除 ||... 行注释。"""
    out = []
    for line in body.splitlines():
        idx = line.find("||")
        out.append(line if idx < 0 else line[:idx])
    return "\n".join(out)


def split_cells(body: str) -> List[Tuple[str, int]]:
    """按逗号切分谱面正文，返回 (cell 内容, 行号)。去掉空白字符。"""
    cells: List[Tuple[str, int]] = []
    buf: List[str] = []
    line = 1
    start_line = 1
    for ch in body:
        if ch == "\n":
            line += 1
            continue
        if ch in " \t\r":
            continue
        if ch == ",":
            cells.append(("".join(buf), start_line))
            buf = []
            start_line = line
            continue
        if not buf:
            start_line = line
        buf.append(ch)
    if buf:
        cells.append(("".join(buf), start_line))
    return cells


DIRECTIVE_RE = re.compile(r"\((?P<bpm>[0-9.]+)\)|\{(?P<div>[^}]+)\}")


def _extract_directives(cell: str, state: Dict[str, float]) -> str:
    """消费 cell 前部/内部的 (bpm) 与 {div} 标记，更新 state，返回剩余音符文本。"""
    def repl(m: re.Match) -> str:
        if m.group("bpm") is not None:
            state["bpm"] = float(m.group("bpm"))
            # BPM 变化后，按分音符定义的逗号长度随之改变
            if state.get("div"):
                state["step"] = _note_len(state["bpm"], state["div"], 1.0)
        else:
            spec = m.group("div").strip()
            if spec.startswith("#"):
                state["div"] = 0.0
                state["step"] = float(spec[1:])
            else:
                state["div"] = float(spec)
                state["step"] = _note_len(state["bpm"], state["div"], 1.0)
        return ""

    return DIRECTIVE_RE.sub(repl, cell)


def _split_each(text: str) -> List[Tuple[str, int]]:
    """把一个 cell 的音符文本拆成 (token, 伪EACH延迟级数)。

    先按 ` 分级（每级延后 1ms），再按 / 拆同时音符。
    """
    out: List[Tuple[str, int]] = []
    for level, group in enumerate(text.split("`")):
        if not group:
            continue
        for token in group.split("/"):
            if token:
                out.append((token, level))
    return out


def _expand_compact_taps(token: str) -> List[str]:
    """'12' 这类纯数字 compact EACH 展开为多个 TAP。"""
    if len(token) > 1 and token.isdigit():
        return list(token)
    return [token]


def _parse_slide_chain(start: str, chunk: str, lengths: List[str],
                       len_cursor: int, time: float, bpm: float,
                       is_break: bool, is_ex: bool, raw: str,
                       warnings: List[str], dense: bool) -> Tuple[Optional[Note], int]:
    """解析一条（可能是连结）SLIDE。返回 (Note, 新的长度游标)。

    命中点 = 星星头 + 每条段的经过键（V 的 via、连结的中间键）+ 终点；
    dense=True 时弧线（> < ^）额外展开沿途每个按钮。
    """
    # (shape, end, via, length_spec)
    segments: List[Tuple[str, int, Optional[int], Optional[str]]] = []
    i = 0
    while i < len(chunk):
        ch = chunk[i]
        if ch == "\x00":
            if segments and len_cursor < len(lengths):
                shape, end, via, _ = segments[-1]
                segments[-1] = (shape, end, via, lengths[len_cursor])
                len_cursor += 1
            i += 1
            continue
        if chunk[i:i + 2] in ("pp", "qq"):
            shape, i = chunk[i:i + 2], i + 2
        elif ch in SHAPE_CHARS:
            shape, i = ch, i + 1
        else:
            warnings.append(f"SLIDE 形状无法识别: {raw}")
            return None, len_cursor
        via = None
        if shape == "V":
            if i + 1 >= len(chunk):
                warnings.append(f"V 型 SLIDE 缺少通过点/终点: {raw}")
                return None, len_cursor
            via = int(chunk[i])
            end = int(chunk[i + 1])
            i += 2
        elif shape == "w":
            end = int(chunk[i]) if i < len(chunk) else int(start)
            i += 1
        else:
            end = int(chunk[i]) if i < len(chunk) else int(start)
            i += 1
        segments.append((shape, end, via, None))

    if not segments:
        return None, len_cursor

    specs = [s[3] for s in segments if s[3]]
    if not specs:
        warnings.append(f"SLIDE 缺少时长，按 1 拍处理: {raw}")
        wait, total_dur = _beat(bpm), _beat(bpm)
        seg_durs: Optional[List[float]] = None
    elif len(specs) == 1:
        wait, total_dur = parse_length(specs[0], bpm, is_slide=True)
        seg_durs = None
    else:
        wait, _ = parse_length(specs[0], bpm, is_slide=True)
        seg_durs = [parse_length(s, bpm, is_slide=True)[1] for s in specs]
        total_dur = sum(seg_durs)

    # 每段路径长度（求比例用）
    seg_lens: List[float] = []
    cur = int(start)
    for shape, end, via, _ in segments:
        seg_lens.append(slide_geometry.segment_len(shape, cur, end, via))
        cur = end
    total_len = sum(seg_lens) or 1.0

    # 命中点与时间
    nodes: List[str] = [start]
    hit_times: List[float] = [time]
    cur = int(start)
    cum_len = 0.0
    cum_time = 0.0
    for seg_i, (shape, end, via, _) in enumerate(segments):
        L = seg_lens[seg_i]
        sd = seg_durs[seg_i] if seg_durs is not None else None
        for btn, frac in slide_geometry.segment_hit_nodes(
                shape, cur, end, via, dense):
            nodes.append(btn)
            if seg_durs is not None:
                t = cum_time + sd * frac
            else:
                t = (cum_len + L * frac) / total_len * total_dur
            hit_times.append(time + wait + t)
        cum_len += L
        if seg_durs is not None:
            cum_time += sd
        cur = end

    note = Note(time, "slide", start, duration=total_dur, wait=wait,
                is_break=is_break, is_ex=is_ex,
                slide_shape="".join(s[0] for s in segments),
                end_position=str(segments[-1][1]),
                slide_nodes=nodes, slide_hit_times=hit_times, raw=raw)
    return note, len_cursor


def _parse_slide_group(start: str, rest: str, lengths: List[str], time: float,
                       bpm: float, is_break: bool, is_ex: bool, raw: str,
                       warnings: List[str], dense: bool) -> List[Note]:
    """解析 SLIDE（含同始点 * 的多条）。星形 TAP 只算一个 note。"""
    notes: List[Note] = []
    cursor = 0
    for chunk in rest.split("*"):
        if not chunk:
            continue
        note, cursor = _parse_slide_chain(start, chunk, lengths, cursor, time,
                                          bpm, is_break, is_ex, raw, warnings,
                                          dense)
        if note:
            notes.append(note)
    return notes


def _parse_token(token: str, time: float, bpm: float,
                 warnings: List[str], dense: bool = False) -> List[Note]:
    """解析单个音符 token（已去除 EACH 分隔符）。返回 0..n 个 Note。"""
    raw = token
    # 1) 提取所有 [..] 长度段，按出现顺序保存，位置用占位符标记
    lengths: List[str] = []

    def take_len(m: re.Match) -> str:
        lengths.append(m.group(1))
        return "\x00"

    body = re.sub(r"\[([^\]]*)\]", take_len, token)

    is_break = "b" in body
    is_ex = "x" in body
    # 2) 去掉纯装饰修饰符（不影响节奏），保留 h 与形状字符
    body = re.sub(r"[bx$@?!f]", "", body)

    # TOUCH / TOUCH HOLD: 以 A-E 开头
    if body and body[0] in TOUCH_AREAS:
        m = re.match(r"^([A-E])([1-8]?)(h?)", body)
        if not m:
            warnings.append(f"无法解析 TOUCH token: {raw}")
            return []
        area, idx, hold = m.group(1), m.group(2), m.group(3)
        pos = area if area == "C" else area + (idx or "1")
        if hold:
            spec = lengths[0] if lengths else f"{PSEUDO_TAP_HOLD_DIV}:1"
            _, dur = parse_length(spec, bpm, is_slide=False)
            return [Note(time, "touch_hold", pos, duration=dur,
                         is_break=is_break, is_ex=is_ex, raw=raw)]
        return [Note(time, "touch", pos, is_break=is_break, is_ex=is_ex,
                     raw=raw)]

    if not body or body[0] not in "12345678":
        warnings.append(f"无法解析 token: {raw}")
        return []

    start = body[0]
    rest = body[1:]

    # HOLD: 按钮号紧跟 h（可能后接长度）
    if rest.startswith("h"):
        spec = lengths[0] if lengths else f"{PSEUDO_TAP_HOLD_DIV}:1"
        _, dur = parse_length(spec, bpm, is_slide=False)
        return [Note(time, "hold", start, duration=dur,
                     is_break=is_break, is_ex=is_ex, raw=raw)]

    if not rest.replace("\x00", ""):
        return [Note(time, "tap", start, is_break=is_break, is_ex=is_ex,
                     raw=raw)]

    return _parse_slide_group(start, rest, lengths, time, bpm, is_break,
                              is_ex, raw, warnings, dense)


def parse_chart(body: str, first: float = 0.0,
                default_bpm: float = 120.0, dense: bool = False) -> ParseResult:
    """解析 simai 谱面正文，返回带绝对秒数的 Note 列表。

    dense=True 时滑星弧线（> < ^）展开沿途每个按钮（更密集的滚奏）。
    """
    result = ParseResult()
    state: Dict[str, float] = {"bpm": default_bpm, "div": 4.0,
                               "step": _note_len(default_bpm, 4.0, 1.0)}
    time = float(first)
    result.bpm_changes.append((time, state["bpm"]))
    last_bpm = state["bpm"]

    for cell, line_no in split_cells(body):
        if cell == "E":
            break
        note_text = _extract_directives(cell, state)
        if state["bpm"] != last_bpm:
            result.bpm_changes.append((time, state["bpm"]))
            last_bpm = state["bpm"]
        # 终止符 E 可与音符同格（如 "1E"）。E 区域 TOUCH 形如 "E1h[..]"，
        # 不会以 E 结尾，因此只剥离结尾的 E。
        is_last = False
        if note_text.endswith("E"):
            note_text = note_text[:-1]
            is_last = True
        if note_text:
            for token, level in _split_each(note_text):
                for sub in _expand_compact_taps(token):
                    t = time + level * PSEUDO_EACH_STEP
                    try:
                        parsed = _parse_token(sub, t, state["bpm"],
                                              result.warnings, dense)
                    except (ValueError, IndexError) as exc:
                        result.warnings.append(
                            f"第 {line_no} 行 token {sub!r} 解析失败: {exc}")
                        continue
                    result.notes.extend(parsed)
        time += state["step"]
        if is_last:
            break

    result.notes.sort(key=lambda n: (n.time, n.position))
    tail = max((n.end_time for n in result.notes), default=time)
    result.end_time = max(time, tail)
    return result


def parse_maidata(text: str) -> Dict[str, str]:
    """解析 maidata.txt 的 &key=value 段（非 & 开头的行续接上一个键）。"""
    data: Dict[str, str] = {}
    key: Optional[str] = None
    for line in text.splitlines():
        if line.startswith("&"):
            body = line[1:]
            if "=" in body:
                key, _, value = body.partition("=")
                key = key.strip().lower()
                data[key] = value
            else:
                key = body.strip().lower()
                data.setdefault(key, "")
        elif key is not None:
            data[key] = data[key] + "\n" + line
    return data


def bpm_at_time(bpm_changes: List[Tuple[float, float]], t: float) -> float:
    """返回 t 时刻生效的 BPM（默认 120）。"""
    bpm = 120.0
    for bt, b in bpm_changes:
        if bt <= t + 1e-9:
            bpm = b
        else:
            break
    return bpm


def resample_dense_notes(notes: List[Note], source_div: int,
                         target_div: int,
                         bpm_changes: List[Tuple[float, float]]) -> List[Note]:
    """把 source_div 分音及更密集的连续音符重采样到 target_div 分音。

    例：source=32、target=16 时，一串 32 分音符降为 16 分音符——密集段内
    按 16 分网格（相对段首）每隔一个保留，既简化节奏又保留足够的节拍参考。
    source_div/target_div 无效（<=0 或 target >= source）时原样返回。
    返回新列表，不修改原列表。
    """
    if not notes or not source_div or not target_div or target_div >= source_div:
        return list(notes)
    ordered = sorted(notes, key=lambda n: n.time)
    result: List[Note] = [ordered[0]]
    prev_time = ordered[0].time
    run_start = ordered[0].time
    last_grid = 0
    for n in ordered[1:]:
        bpm = bpm_at_time(bpm_changes, n.time)
        src_int = 240.0 / (bpm * source_div)
        tgt_int = 240.0 / (bpm * target_div)
        gap = n.time - prev_time
        if gap <= src_int + 1e-6:
            # 密集段：按 target 网格重采样（相对段首）
            g = int((n.time - run_start) / tgt_int + 1e-9)
            if g > last_grid:
                result.append(n)
                last_grid = g
            prev_time = n.time
        else:
            # 新的密集段（或孤立音）
            result.append(n)
            prev_time = n.time
            run_start = n.time
            last_grid = 0
    return result
