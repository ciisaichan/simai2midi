"""回读生成的 MIDI，校验时间轴与解析结果一致。

用法: python verify_midi.py <maidata.txt> <difficulty> <out.mid>
"""
from __future__ import annotations

import sys

import mido

import midi_writer
import simai_parser as sp


def midi_onsets(path: str) -> list[float]:
    """读回 MIDI，返回所有 note_on 的绝对秒数。"""
    mf = mido.MidiFile(path)
    onsets = []
    t = 0.0
    for msg in mf:  # mido 迭代已按 tempo 折算为秒
        t += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            onsets.append(t)
    return onsets


def main() -> int:
    src, diff, mid = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    with open(src, encoding="utf-8", errors="replace") as fh:
        data = sp.parse_maidata(fh.read())
    first = float(data.get("first", "0") or 0)
    body = sp.strip_comments(data[f"inote_{diff}"])
    res = sp.parse_chart(body, first=first,
                         default_bpm=float(data.get("wholebpm", "120") or 120))

    expect = sorted(n.time for n in res.notes)
    got = midi_onsets(mid)

    print(f"解析 note 数: {len(expect)}   MIDI note_on 数: {len(got)}")
    if len(expect) != len(got):
        print("数量不一致", file=sys.stderr)
        return 1

    # 检查所有通道/音色
    mf = mido.MidiFile(mid)
    chans, pitches = set(), set()
    for msg in mf:
        if msg.type in ("note_on", "note_off"):
            chans.add(msg.channel)
            pitches.add(msg.note)
    print(f"通道: {sorted(chans)} (9 = GM 打击乐)  音色: {sorted(pitches)}")

    worst = 0.0
    worst_at = 0.0
    for e, g in zip(expect, got):
        d = abs(e - g)
        if d > worst:
            worst, worst_at = d, e
    print(f"最大时间误差: {worst * 1000:.3f} ms (在 {worst_at:.3f}s)")
    print(f"首音 解析={expect[0]:.4f}s MIDI={got[0]:.4f}s")
    print(f"末音 解析={expect[-1]:.4f}s MIDI={got[-1]:.4f}s")

    # 480 tick/beat 下，单 tick 在 BPM 200 时约 0.625ms，允许 2ms 量化误差
    if worst > 0.002:
        print("时间误差超过 2ms 阈值", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
