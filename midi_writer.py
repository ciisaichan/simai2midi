"""把 simai Note 事件写成打击乐（拍手）MIDI。

通道 10（channel index 9）为 GM 打击乐通道。默认全部音符映射到
Hand Clap (39)，可按 note 类型分配不同打击音色以便区分。
"""
from __future__ import annotations

import os
import tempfile
from typing import Dict, Iterable, List, Optional, Tuple

import mido

from simai_parser import Note

TICKS_PER_BEAT = 480
DRUM_CHANNEL = 9

# GM 打击乐音符号
HAND_CLAP = 39
CLOSED_HH = 42
OPEN_HH = 46
CRASH = 49
TAMBOURINE = 54
COWBELL = 56

# 默认：全部拍手（需要按音符类型分轨时用 --distinct）
DEFAULT_MAP: Dict[str, int] = {
    "tap": HAND_CLAP,
    "hold": HAND_CLAP,
    "slide": HAND_CLAP,
    "touch": HAND_CLAP,
    "touch_hold": HAND_CLAP,
    "break": HAND_CLAP,
}

# 可选：按类型区分音色（--distinct）
DISTINCT_MAP: Dict[str, int] = {
    "tap": HAND_CLAP,
    "hold": COWBELL,
    "slide": TAMBOURINE,
    "touch": CLOSED_HH,
    "touch_hold": OPEN_HH,
    "break": CRASH,
}


class TickMap:
    """秒 → tick 的分段线性映射，与 BPM 变化保持一致。"""

    def __init__(self, bpm_changes: List[Tuple[float, float]]):
        if not bpm_changes:
            bpm_changes = [(0.0, 120.0)]
        changes = sorted(bpm_changes, key=lambda c: c[0])
        # 合并同一时刻的重复标记，只保留最后一个
        merged: List[Tuple[float, float]] = []
        for sec, bpm in changes:
            if merged and abs(merged[-1][0] - sec) < 1e-9:
                merged[-1] = (sec, bpm)
            else:
                merged.append((sec, bpm))
        self.segments: List[Tuple[float, float, float]] = []  # (sec, tick, bpm)
        tick = 0.0
        prev_sec, prev_bpm = merged[0][0], merged[0][1]
        # 第一个 BPM 标记之前的时间（first 偏移）按首个 BPM 折算
        base_sec = 0.0
        tick = (prev_sec - base_sec) * prev_bpm / 60.0 * TICKS_PER_BEAT
        self.segments.append((prev_sec, tick, prev_bpm))
        for sec, bpm in merged[1:]:
            tick += (sec - prev_sec) * prev_bpm / 60.0 * TICKS_PER_BEAT
            self.segments.append((sec, tick, bpm))
            prev_sec, prev_bpm = sec, bpm

    def tick(self, sec: float) -> int:
        seg = self.segments[0]
        for candidate in self.segments:
            if candidate[0] <= sec + 1e-9:
                seg = candidate
            else:
                break
        seg_sec, seg_tick, bpm = seg
        if sec < self.segments[0][0]:
            bpm = self.segments[0][2]
            return max(0, int(round(sec * bpm / 60.0 * TICKS_PER_BEAT)))
        return int(round(seg_tick + (sec - seg_sec) * bpm / 60.0
                         * TICKS_PER_BEAT))

    def tempo_events(self) -> List[Tuple[int, int]]:
        return [(int(round(t)), mido.bpm2tempo(bpm))
                for _, t, bpm in self.segments]


def note_hit_times(note: Note, tails: bool = False) -> List[float]:
    """一个音符对应的节奏命中时刻（拍手点）。

    - tap/touch: 1 击（头）
    - hold/touch_hold: 头；tails=True 时再加尾判（结束时刻）
    - slide: 星星头 + 经过键（连结/V 的中间键）；tails=True 时再加终点尾判
    """
    if note.kind == "slide":
        hits = note.slide_hit_times or [note.time, note.end_time]
        if not tails and len(hits) > 1:
            hits = hits[:-1]  # 去掉终点尾判
        return hits
    if note.kind in ("hold", "touch_hold"):
        return [note.time, note.end_time] if tails else [note.time]
    return [note.time]


def count_hits(notes: Iterable[Note], tails: bool = False) -> int:
    return sum(len(note_hit_times(n, tails)) for n in notes)


def build_midi(notes: Iterable[Note],
               bpm_changes: List[Tuple[float, float]],
               distinct: bool = False,
               hold_sustain: bool = False,
               tails: bool = False,
               velocity: int = 100,
               break_velocity: int = 127,
               title: Optional[str] = None) -> mido.MidiFile:
    """生成打击乐 MIDI。

    - distinct: 按 note 类型使用不同打击音色（默认全部拍手）
    - hold_sustain: HOLD/TOUCH HOLD 头击按真实时长持续（否则短促一击）
    - tails: 包含 HOLD / SLIDE 的尾判音（默认不含）
    """
    mapping = DISTINCT_MAP if distinct else DEFAULT_MAP
    tmap = TickMap(bpm_changes)

    mf = mido.MidiFile(ticks_per_beat=TICKS_PER_BEAT)
    meta = mido.MidiTrack()
    mf.tracks.append(meta)
    if title:
        meta.append(mido.MetaMessage("track_name", name=title[:120], time=0))
    prev = 0
    for tick, tempo in tmap.tempo_events():
        meta.append(mido.MetaMessage("set_tempo", tempo=tempo,
                                     time=max(0, tick - prev)))
        prev = max(prev, tick)

    track = mido.MidiTrack()
    mf.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="maimai clap", time=0))

    # (tick, 0=note_off/1=note_on, pitch, velocity)
    events: List[Tuple[int, int, int, int]] = []
    hit_len = TICKS_PER_BEAT // 8  # 短促一击

    for note in notes:
        pitch = mapping.get("break" if note.is_break else note.kind,
                            HAND_CLAP)
        vel = break_velocity if note.is_break else velocity
        for htime in note_hit_times(note, tails):
            on = tmap.tick(htime)
            is_head = htime == note.time
            if hold_sustain and note.kind in ("hold", "touch_hold") and is_head:
                off = max(on + 1, tmap.tick(note.end_time))
            else:
                off = on + hit_len
            events.append((on, 1, pitch, vel))
            events.append((off, 0, pitch, 0))

    events.sort(key=lambda e: (e[0], e[1]))
    last = 0
    for tick, is_on, pitch, vel in events:
        delta = max(0, tick - last)
        last = tick
        msg_type = "note_on" if is_on else "note_off"
        track.append(mido.Message(msg_type, channel=DRUM_CHANNEL, note=pitch,
                                  velocity=vel, time=delta))
    return mf


def save_midi(mf: mido.MidiFile, path: str, charset: str = "utf-8") -> None:
    """原子保存 MIDI，支持日文/中文等非 latin-1 曲名。

    两个已踩过的坑：
    1. MidiFile 默认 charset='latin1'，且 MidiFile._save 用
       `with meta_charset(self.charset)` 包裹写出——它会覆盖模块级
       _charset，所以必须改实例属性而不是全局变量。
    2. mf.save() 中途抛错会留下只含 14 字节 MThd 头的损坏文件。
       先写临时文件再 os.replace，失败时目标路径保持原样。
    """
    old = mf.charset
    mf.charset = charset
    directory = os.path.dirname(os.path.abspath(path))
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".mid.tmp", dir=directory)
        os.close(fd)
        mf.save(tmp)
        os.replace(tmp, path)
        tmp = None
    finally:
        mf.charset = old
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
