# simai2midi

把 maimai 街机谱面（simai 记谱 / maidata.txt）转换为打击乐 MIDI。
所有音符默认映射到 GM 打击乐通道的 Hand Clap（拍手，note 39）。

## 环境

```bash
conda activate base   # 需要 mido
```

## 用法

```bash
# 默认导出最高难度
python simai2midi.py maidata.txt -o out.mid

# 指定难度槽位（1=Easy 2=Basic 3=Advanced 4=Expert 5=Master 6=ReMaster 7=UTAGE）
python simai2midi.py maidata.txt -d 4 -o expert.mid

# 导出全部难度到目录（按 曲名_难度.mid 命名）
python simai2midi.py maidata.txt --all -O out/

# 裸 inote 正文（无 & 头部的文件），需手动给 BPM 与偏移
python simai2midi.py inote.simai --bpm 174 --first 1.234 -o out.mid
```

主要选项：

| 选项 | 说明 |
| --- | --- |
| `--first SEC` / `--no-offset` | 覆盖 / 忽略 `&first` 起始偏移 |
| `--bpm N` | 正文未写 `(bpm)` 时的默认 BPM（默认读 `&wholebpm`，否则 120） |
| `--distinct` | 按音符类型分配不同打击音色，便于在 DAW 里区分 |
| `--hold-sustain` | HOLD / TOUCH HOLD 按真实时长持续（默认短促一击） |
| `--tails` | 包含 HOLD / SLIDE 的尾判音（默认不含） |
| `--slide-dense` | 滑星弧线（`>` `<` `^`）展开沿途每个按钮（更密集的滚奏） |
| `--velocity` / `--break-velocity` | 力度（默认 100 / 127） |

### 节奏命中（拍手点）与尾判

解析器把每个音符展开成若干「拍手点」：

- TAP / TOUCH：1 点（头）
- HOLD / TOUCH HOLD：1 点（头）；`--tails` 时再加尾判（结束时刻）
- SLIDE：头 + 经过键（连结 SLIDE 的中间键、大 V 的 via）；`--tails` 时再加终点尾判

默认（不含尾判）导出的是一份「纯打击节奏」——与游戏判定里每个
需要按下的时刻对应；加上 `--tails` 后，HOLD 的离手点与 SLIDE 的到达点
也会变成一拍，更贴近游戏判定节奏的完整形态。

`--distinct` 的音色分配：TAP=39 拍手、HOLD=56 牛铃、SLIDE=54 铃鼓、
TOUCH=42 闭合踩镲、TOUCH HOLD=46 开放踩镲、BREAK=49 碰铃。

## 支持的 simai 语法

- BPM 与时值：`(bpm)`、`{div}`、`{#秒}`，可在谱面任意处切换（写入 MIDI tempo 变化）
- TAP：`1`、BREAK `1b`、EX `1x`、星形 `1$`/`1$$`、组合修饰符任意顺序
- HOLD：`5h[2:1]`、`4h[#5.678]`、`4h[150#2:1]`、伪 TAP `3h`（= `[1280:1]`）
- SLIDE：全部形状 `- > < ^ v p q s z pp qq V w`；时长 `[8:3]`、`[160#8:3]`、
  `[160#2]`、`[3##1.5]`、`[3##8:3]`、`[3##160#8:3]`；星星等待默认 1 拍
- 同始点 SLIDE `1-4[4:3]*-6[8:5]`（计 2 个音符）、
  连结 SLIDE `1-4q7-2[1:2]`（计 1 个音符，时长相加）
- TOUCH / TOUCH HOLD：`B1`、`D4`、`C`（`C1`/`C2` 归一为 `C`）、`E1h[4:3]`、花火 `B7f`
- EACH：`1/8h[2:1]`、纯 TAP 简写 `12`
- 伪 EACH：`1`2`3/4`（每级延后 1ms）
- 星形抑制 `?` / `!`、星形转普通 TAP `@`
- 终止符 `E`、`||` 行注释、正文内换行与空白

## 时间轴处理

解析器先把每个音符换算为绝对秒数（含 `first` 偏移），再由 `TickMap`
做分段线性的秒 → tick 映射，使 MIDI tempo 事件与谱面 BPM 变化对齐。
tick 分辨率为 480/拍。

## 非 ASCII 曲名

日文/中文曲名需要特别处理，工具已内置：

- mido 的 `MidiFile` 默认 `charset='latin1'`，`_save` 用
  `with meta_charset(self.charset)` 包裹写出，所以改模块级 `_charset`
  无效，必须设实例的 `charset`。`midi_writer.save_midi()` 负责这件事。
- 写出采用「临时文件 + `os.replace`」的原子方式。直接 `mf.save()` 若在
  编码 track_name 时抛错，会留下只含 14 字节 MThd 头的损坏 .mid，
  后续读取报 `EOFError`。
- **读回时也要显式指定** `mido.MidiFile(path, charset="utf-8")`，
  否则 track_name 会按 latin-1 解码成乱码。音符数据不受影响。

## 测试与校验

```bash
python test_simai_parser.py                          # 23 项解析器测试（用例取自官方文档）
python test_midi_writer.py                           # 10 项写出测试（含非 ASCII 曲名、尾判开关与原子保存）
python verify_midi.py <maidata.txt> <难度> <out.mid>  # 单文件回读比对
python verify_all.py                                 # 批量回读 samples/ 与 out/
```

`verify_*.py` 会重新解析源谱，把 MIDI 里读回的 note_on 绝对秒数与解析
结果逐一比对，同时检查通道与音色。

实测结果（`verify_all.py`，36 个谱面 / 0 失败 / 0 解析警告）：

| 谱面 | 难度数 | 最大时间误差 |
| --- | --- | --- |
| AMABIE | 4 | 0.205 ms |
| HECATONCHEIR（6 段 BPM 变化） | 4 | 0.163 ms |
| アンビバレンス | 5 | 0.313 ms |
| シスターシスター | 4 | 0.164 ms |
| Absolute Queen | 4 | 0.262 ms |
| Inverted World | 4 | 0.252 ms |
| Cryogenic | 2 | 0.416 ms |
| PANDORA PARADOXXX | 5 | 0.612 ms |
| インターネットサバイバー | 4 | 0.315 ms |

误差来源是 480 tick/拍的量化，最大 0.6 ms 仍远低于任何听感阈值。

## 文件

- `simai_parser.py` — simai 语法解析，输出带绝对秒数的 `Note` 列表
- `slide_geometry.py` — 滑星几何：按钮位置、弧线方向、路径长度（用于命中点展开）
- `midi_writer.py` — `TickMap` 时间映射与打击乐 MIDI 生成
- `simai2midi.py` — CLI 入口
- `test_simai_parser.py` / `test_midi_writer.py` / `verify_midi.py` / `verify_all.py` — 测试与校验
- `samples/` — 测试用谱面；`out/` — 生成的 MIDI
