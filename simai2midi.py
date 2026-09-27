#!/usr/bin/env python3
"""simai 谱面 → 打击乐（拍手）MIDI 转换工具。

用法:
    python simai2midi.py maidata.txt -d 4 -o out.mid
    python simai2midi.py maidata.txt --all          # 导出全部难度
    python simai2midi.py inote.simai --bpm 174 --first 1.234
    python simai2midi.py maidata.txt --all --musicxml --pdf   # 附带节奏谱
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from typing import Dict, List, Optional, Tuple

import midi_writer
import score_writer
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
    p.add_argument("--musicxml", action="store_true",
                   help="同时导出 MusicXML 乐谱（*.musicxml）")
    p.add_argument("--pdf", action="store_true",
                   help="同时导出乐谱 PDF（*.pdf，由 MusicXML 渲染）")
    p.add_argument("--time-sig", default="4/4", metavar="N/D",
                   help="乐谱拍号（默认 4/4）")
    p.add_argument("--score-grid", type=int, default=score_writer.DEFAULT_GRID_DIV,
                   metavar="N",
                   help="乐谱量化网格，N 为分音分母（默认 192=10 tick；"
                        "192=64×3 能同时精确表示 32 分与三连 16 分，"
                        "换成 64 会写错三连音时值；0=不量化，可能无法记谱）")
    p.add_argument("--score-note-div", type=int,
                   default=score_writer.DEFAULT_NOTE_DIV, metavar="N",
                   help="谱面短符头时值，N 为分音分母（默认 8=八分音符；"
                        "16 更短、休止符更多）")
    p.add_argument("--score-binary", action="store_true",
                   help="乐谱只用二进制时值（不写 <time-modification> 连音），"
                        "拍点吸附到 128 分网格（误差 ≤7.5 tick ≈6ms）。"
                        "music21 生成的连音记谱是补丁式的、连音组不合法，"
                        "默认路径已按拍整组重写、能同时满足 MuseScore 与连音记号；"
                        "此选项是最后的逃生通道：完全不写连音，代价是三连音被展开")
    p.add_argument("--velocity", type=int, default=100, help="普通音符力度")
    p.add_argument("--break-velocity", type=int, default=127,
                   help="BREAK 音符力度")
    p.add_argument("-q", "--quiet", action="store_true", help="只输出结果路径")
    return p


def score_meta(data: Dict[str, str], slot: int, title: str) -> Dict[str, str]:
    """从 maidata 组装节奏谱的标题块。

    主标题=曲名，副标题=「Lv.13.7 ReMaster」，左侧=谱师，右侧=曲作者。
    """
    level = data.get(f"lv_{slot}", "").strip() if slot else ""
    diff = DIFF_NAMES.get(slot, "")
    if level and diff:
        subtitle = f"Lv.{level} {diff}"
    else:
        subtitle = level or diff
    designer = data.get(f"des_{slot}", "").strip() if slot else ""
    return {
        "title": title,
        "subtitle": subtitle,
        "composer": data.get("artist", "").strip(),
        "designer": f"譜面: {designer}" if designer else "",
    }


def export_score(result: simai_parser.ParseResult, args,
                 mid_path: str, meta: Dict[str, str]) -> List[str]:
    """按需导出 MusicXML / PDF 乐谱；返回额外产物路径列表。"""
    if not (args.musicxml or args.pdf):
        return []
    try:
        time_sig = score_writer.parse_time_sig(args.time_sig)
    except ValueError as exc:
        raise SystemExit(f"--time-sig 无效：{exc}")

    base = os.path.splitext(mid_path)[0]
    note_div = args.score_note_div or score_writer.DEFAULT_NOTE_DIV
    try:
        if note_div <= 0:
            raise ValueError(f"分音分母必须为正整数，得到 {note_div!r}")
        grid = score_writer.grid_from_div(args.score_grid)
    except ValueError as exc:
        raise SystemExit(f"乐谱参数无效：{exc}")
    hits = score_writer.collect_hits(result.notes, tails=args.tails)
    score = score_writer.build_score(hits, result.bpm_changes,
                                     title=meta.get("title", ""),
                                     subtitle=meta.get("subtitle", ""),
                                     composer=meta.get("composer", ""),
                                     designer=meta.get("designer", ""),
                                     time_sig=time_sig,
                                     grid_ticks=grid,
                                     note_div=note_div,
                                     binary_only=getattr(args, "score_binary",
                                                         False))

    produced: List[str] = []
    # MusicXML 的 <volume> 是 0–100 的百分比；MuseScore 从 MIDI 导入时
    # 用 velocity/127*100 填（velocity 100 → 78.7402），这里照做。
    volume = args.velocity / 127 * 100
    xml_path = f"{base}.musicxml"
    if args.musicxml:
        score_writer.save_musicxml(score, xml_path, volume=volume)
        produced.append(xml_path)

    if args.pdf:
        pdf_path = f"{base}.pdf"
        if args.musicxml:
            source = xml_path
        else:  # PDF 单独用时，MusicXML 只是中间产物
            handle, source = tempfile.mkstemp(prefix="simai-score-",
                                              suffix=".musicxml")
            os.close(handle)
            score_writer.save_musicxml(score, source, volume=volume)
        try:
            score_writer.render_pdf(source, pdf_path, header=meta)
        except (RuntimeError, OSError) as exc:
            raise SystemExit(f"渲染 PDF 失败：{exc}")
        finally:
            if not args.musicxml and os.path.exists(source):
                os.unlink(source)
        produced.append(pdf_path)
    return produced


def convert_one(body: str, slot: int, args, data: Dict[str, str],
                out_path: str) -> Tuple[int, int, simai_parser.ParseResult,
                                        List[str]]:
    """转换单个难度。返回 (原始音符数, 重采样后音符数, ParseResult, 乐谱产物)。"""
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
    produced = export_score(result, args, out_path,
                            score_meta(data, slot, title))
    return original, len(result.notes), result, produced


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
        original, count, result, produced = convert_one(chart_body, slot, args,
                                                        data, out_path)
        if args.quiet:
            for path in [out_path] + produced:
                print(path)
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
        for path in produced:
            print(f"   乐谱: {path}")
        for w in result.warnings[:10]:
            print(f"  警告: {w}", file=sys.stderr)
        if len(result.warnings) > 10:
            print(f"  ... 另有 {len(result.warnings) - 10} 条警告",
                  file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
