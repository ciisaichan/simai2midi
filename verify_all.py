
"""批量回读校验：对每个 samples/*.txt 的每个难度，重新解析并与已生成 MIDI 比对。"""
import glob, os, sys
import mido
import simai_parser as sp
import midi_writer as mw

DIFF = {1: "Easy", 2: "Basic", 3: "Advanced", 4: "Expert",
        5: "Master", 6: "ReMaster", 7: "UTAGE"}

SEARCH_DIRS = ("out", "out/remote", "out/ambivalence", "out/regress")

def onsets(path):
    # charset="utf-8" 与 save_midi 的写出编码一致；非 ASCII 曲名用默认
    # latin-1 读回会得到乱码的 track_name。
    t, out = 0.0, []
    for msg in mido.MidiFile(path, charset="utf-8"):
        t += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            out.append(t)
    return out

def safe(name):
    keep = " ._-()[]"
    return "".join(c if c.isalnum() or c in keep else "_" for c in name).strip()

fails = 0
checked = 0
for src in sorted(glob.glob("samples/*.txt")):
    data = sp.parse_maidata(open(src, encoding="utf-8", errors="replace").read())
    if "title" not in data:
        continue
    title = safe(data["title"].strip())
    first = float(data.get("first", "0") or 0)
    try:
        bpm = float(data.get("wholebpm", "") or 0) or 120.0
    except ValueError:
        bpm = 120.0
    for slot in range(1, 8):
        body = data.get(f"inote_{slot}", "").strip()
        if not body:
            continue
        for d in SEARCH_DIRS:
            mid = os.path.join(d, f"{title}_{DIFF[slot]}.mid")
            if os.path.exists(mid):
                break
        else:
            continue
        res = sp.parse_chart(sp.strip_comments(body), first=first, default_bpm=bpm)
        exp = sorted(ht for n in res.notes for ht in mw.note_hit_times(n))
        got = onsets(mid)
        checked += 1
        tag = f"{title} {DIFF[slot]}"
        if len(exp) != len(got):
            print(f"FAIL {tag}: 数量 解析={len(exp)} MIDI={len(got)}")
            fails += 1
            continue
        worst = max((abs(a - b) for a, b in zip(exp, got)), default=0.0)
        chans, pitches = set(), set()
        for msg in mido.MidiFile(mid):
            if msg.type in ("note_on", "note_off"):
                chans.add(msg.channel); pitches.add(msg.note)
        bad = chans != {9} or pitches != {39}
        if worst > 0.002 or bad:
            print(f"FAIL {tag}: 误差={worst*1000:.3f}ms ch={sorted(chans)} pitch={sorted(pitches)}")
            fails += 1
        else:
            print(f"PASS {tag}: {len(exp)} notes, 误差={worst*1000:.3f}ms, "
                  f"警告={len(res.warnings)}")
        if res.warnings:
            for w in res.warnings[:3]:
                print(f"     警告: {w}")

print(f"\n校验 {checked} 个文件, {fails} 个失败")
sys.exit(1 if fails else 0)
