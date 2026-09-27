#!/usr/bin/env python3
"""simai 谱面 → 打击乐（拍手）MIDI 转换工具。

用法:
    python simai2midi.py maidata.txt -d 4 -o out.mid
    python simai2midi.py maidata.txt --all          # 导出全部难度
    python simai2midi.py inote.simai --bpm 174 --first 1.234
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List, Optional, Tuple

import midi_writer
import simai_parser

DIFF_NAMES = {
    1: "Easy", 2: "Basic", 3: "Advanced", 4: "Expert",
    5: "Master", 6: "ReMaster", 7: "UTAGE",
}


def _safe(name: str) -> str:
    keep = " ._-()[]"
    out = "".join(c if c.isalnum() or c in keep else "_" for c in name)
    return out.strip() or "chart"


def load_source(path: str) -> Tuple[Dict[str, str], Optional[str]]:
    """读取文件。返回 (maidata 键值表, 裸谱面正文)。"""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    if text.lstrip().startswith("&") or "\n&" in text:
        return simai_parser.parse_maidata(text), None
    return {}, text


def collect_charts(data: Dict[str, str], body: Optional[str],
                   want: Optional[List[int]] = None,
                   all_diffs: bool = False) -> List[Tuple[int, str]]:
    """返回 [(难度槽位, 谱面正文)]。裸正文的槽位记为 0。"""
    if body is not None:
        return [(0, body)]
    available = sorted(int(k[6:]) for k in data
                       if k.startswith("inote_") and k[6:].isdigit()
                       and data[k].strip())
    if not available:
        return []
    if all_diffs:
        picked = available
    elif want:
        missing = [d for d in want if d not in available]
        if missing:
            raise SystemExit(
                f"文件中没有难度 {missing}；可用: {available}")
        picked = want
    else:
        picked = [max(available)]
    return [(d, data[f"inote_{d}"]) for d in picked]


def _resample_spec(s: str) -> Tuple[int, int]:
    """解析 --resample 的 "S/T" 参数为 (source_div, target_div)。"""
    if "/" not in s:
        raise argparse.ArgumentTypeError(f"需要 S/T 格式（如 32/16），得到 {s!r}")
    a, _, b = s.partition("/")
    try:
        src, tgt = int(a), int(b)
    except ValueError:
        raise argparse.ArgumentTypeError(f"无法解析为整数: {s!r}")
    if src <= 0 or tgt <= 0 or tgt >= src:
        raise argparse.ArgumentTypeError(f"需满足 0 < T < S（如 32/16），得到 {s!r}")
    return (src, tgt)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="把 maimai simai 谱面转换为拍手打击乐 MIDI")
    p.add_argument("input", help="maidata.txt 或裸 inote 正文文件")
    p.add_argument("-o", "--output", help="输出 .mid 路径（单难度时有效）")
    p.add_argument("-O", "--outdir", default=".", help="批量输出目录")
    p.add_argument("-d", "--difficulty", type=int, action="append",
                   help="难度槽位 1-7，可重复；默认取最高难度")
    p.add_argument("--all", action="store_true", help="导出文件内全部难度")
    p.add_argument("--bpm", type=float,
                   help="默认 BPM（正文未写 (bpm) 时使用；默认读 &wholebpm 或 120）")
    p.add_argument("--first", type=float,
                   help="起始偏移秒；默认读 &first")
    p.add_argument("--no-offset", action="store_true",
                   help="忽略 first，谱面从 0 秒开始")
    p.add_argument("--distinct", action="store_true",
                   help="按 note 类型使用不同打击音色（默认全部拍手）")
    p.add_argument("--hold-sustain", action="store_true",
                   help="HOLD/TOUCH HOLD 按真实时长持续（默认短促一击）")
    p.add_argument("--slide-dense", action="store_true",
                   help="滑星弧线（> < ^）展开沿途每个按钮（更密集的滚奏）")
    p.add_argument("--tails", action="store_true",
                   help="包含 HOLD / SLIDE 的尾判音（默认不含）")
    p.add_argument("--resample", type=_resample_spec, default=None, metavar="S/T",
                   help="把 S 分音及更密集的连续音符重采样到 T 分音"
                        "（如 32/16 将 32 分降为 16 分；默认不处理）")
    p.add_argument("--velocity", type=int, default=100, help="普通音符力度")
    p.add_argument("--break-velocity", type=int, default=127,
                   help="BREAK 音符力度")
    p.add_argument("-q", "--quiet", action="store_true", help="只输出结果路径")
    return p


def convert_one(body: str, slot: int, args, data: Dict[str, str],
                out_path: str) -> Tuple[int, int, simai_parser.ParseResult]:
    """转换单个难度。返回 (原始音符数, 合并后音符数, ParseResult)。"""
    first = 0.0
    if not args.no_offset:
        if args.first is not None:
            first = args.first
        else:
            try:
                first = float(data.get("first", "0") or 0)
            except ValueError:
                first = 0.0
    bpm = args.bpm
    if bpm is None:
        try:
            bpm = float(data.get("wholebpm", "") or 0) or 120.0
        except ValueError:
            bpm = 120.0

    clean = simai_parser.strip_comments(body)
    result = simai_parser.parse_chart(clean, first=first, default_bpm=bpm,
                                      dense=args.slide_dense)
    original = len(result.notes)
    if args.resample:
        src, tgt = args.resample
        result.notes = simai_parser.resample_dense_notes(
            result.notes, src, tgt, result.bpm_changes)
    title = data.get("title", os.path.basename(args.input)).strip()
    label = f"{title} [{DIFF_NAMES.get(slot, 'chart')}]" if slot else title
    mf = midi_writer.build_midi(
        result.notes, result.bpm_changes,
        distinct=args.distinct,
        hold_sustain=args.hold_sustain,
        tails=args.tails,
        velocity=args.velocity,
        break_velocity=args.break_velocity,
        title=label)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    midi_writer.save_midi(mf, out_path)
    return original, len(result.notes), result


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    data, body = load_source(args.input)
    charts = collect_charts(data, body, args.difficulty, all_diffs=args.all)
    if not charts:
        print("未找到谱面正文（缺少 &inote_N）", file=sys.stderr)
        return 1

    title = data.get("title", os.path.splitext(
        os.path.basename(args.input))[0]).strip()
    for slot, chart_body in charts:
        if args.output and len(charts) == 1:
            out_path = args.output
        else:
            suffix = f"_{DIFF_NAMES.get(slot, 'chart')}" if slot else ""
            out_path = os.path.join(args.outdir,
                                    f"{_safe(title)}{suffix}.mid")
        original, count, result = convert_one(chart_body, slot, args, data,
                                              out_path)
        if args.quiet:
            print(out_path)
            continue
        kinds: Dict[str, int] = {}
        for n in result.notes:
            kinds[n.kind] = kinds.get(n.kind, 0) + 1
        detail = " ".join(f"{k}={v}" for k, v in sorted(kinds.items()))
        hits = midi_writer.count_hits(result.notes, tails=args.tails)
        merged = ""
        if args.resample:
            src, tgt = args.resample
            merged = f" (重采样{src}→{tgt}分音: {original}→{count})"
        print(f"{out_path}: {count} notes / {hits} 拍手点{merged} ({detail}), "
              f"时长 {result.end_time:.2f}s, "
              f"BPM 段 {len(result.bpm_changes)}")
        for w in result.warnings[:10]:
            print(f"  警告: {w}", file=sys.stderr)
        if len(result.warnings) > 10:
            print(f"  ... 另有 {len(result.warnings) - 10} 条警告",
                  file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
