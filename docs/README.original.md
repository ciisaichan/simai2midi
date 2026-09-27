# simai2midi

把 maimai 街机谱面（simai 记谱 / maidata.txt）转换为打击乐 MIDI。
所有音符默认映射到 GM 打击乐通道的 Hand Clap（拍手，note 39）。

可选附带导出**节奏谱**：MusicXML（`--musicxml`）与 PDF（`--pdf`），
打击乐单线谱，每个音符都是短符头 + 显式休止符，标题区带曲名/难度/作者。

## 环境

```bash
conda activate base   # 需要 mido
```

导出乐谱还需要：

```bash
pip install music21 verovio cairosvg pypdf
brew install cairo     # macOS：cairosvg 需要系统 libcairo
```

只导出 MIDI 时这些依赖都不需要（乐谱相关 import 全部延迟到调用时）。

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

# 附带导出节奏谱（MusicXML + PDF）
python simai2midi.py maidata.txt --all -O out/ --musicxml --pdf
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
| `--resample S/T` | 把 S 分音及更密集的连续音符重采样到 T 分音（如 32/16；默认不处理） |
| `--musicxml` | 同时导出节奏谱 MusicXML（`*.musicxml`） |
| `--pdf` | 同时导出节奏谱 PDF（`*.pdf`，由 MusicXML 渲染） |
| `--time-sig N/D` | 乐谱拍号（默认 `4/4`） |
| `--score-grid N` | 乐谱量化网格，N 为分音分母（默认 `192`=10 tick，兼容三连音系分工；`64` 更简但会写错三连音时值、`0` 不量化） |
| `--score-note-div N` | 谱面短符头时值，N 为分音分母（默认 `8`=八分音符；`16` 更短、休止符更多） |
| `--score-binary` | 兼容模式：只用二进制时值、不产生连音（拍点吸附到 128 分网格）。MuseScore 一定能打开，代价是三连音被展开成等值音符 |
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

### 扫键重采样（`--resample S/T`）

maimai 谱面里的「扫键」是一串快速连续音符（32 分、64 分等），实际演奏时
是扫过而非逐个敲击。`--resample S/T` 会把「S 分音及更密集」的连续音符
**重采样到 T 分音**：密集段内按 T 分网格（相对段首）每隔一段保留一拍，
既简化了节奏，又比「只留第一个」保留更多节拍参考。

- S 分音符时长 = `240/(BPM×S)` 秒；相邻音符间隔不超过该时长即视为同一密集段。
- 密集段内按 `240/(BPM×T)` 的网格保留音符（如 32/16 每隔一个 32 分留一个）。
- BPM 变化时按当前位置的 BPM 计算阈值；`S/T` 需满足 `0 < T < S`。
- 默认不启用（省略 `--resample` 时不处理）。

```bash
# 把 32 分音及更密集（扫键）降采样到 16 分音
python simai2midi.py maidata.txt --all -O out/ --resample 32/16

# 更激进：64 分音降采样到 16 分音
python simai2midi.py maidata.txt --all -O out/ --resample 64/16
```

## 节奏谱导出（`--musicxml` / `--pdf`）

`--musicxml` 写出 `.musicxml`，`--pdf` 写出 `.pdf`（与 `.mid` 同名同目录）。
两者都基于**与 MIDI 完全相同的拍手点**，因此谱面与 MIDI、与游戏判定一致。
`--pdf` 单独使用时 MusicXML 只作为临时中间文件，用完即删。

### 渲染链路

```
拍手点 → music21（乐谱） → MusicXML → verovio（SVG，逐页） → cairosvg（PDF） → pypdf（合并多页）
```

verovio 无原生 PDF 输出，所以走 SVG 转 PDF。cairosvg 默认**透明背景**，
深色阅读器里会变成黑底黑谱，因此 `render_pdf` 显式传了白色背景。

macOS 上 `cairocffi` 靠 `ctypes.util.find_library('cairo')` 找 libcairo，
而该函数会读取 `DYLD_FALLBACK_LIBRARY_PATH`——homebrew 的
`/opt/homebrew/lib` 不在 dyld 默认搜索路径里。`score_writer` 会在
**import cairosvg 之前**把该目录补进环境变量，所以无需手工 `export`。

### 谱面形态

- **打击乐单线谱**：打击乐谱号 + `<staff-lines>1</staff-lines>` + `<unpitched>` 音符；
  符头的显示音位显式写成 **E4**（verovio 把那条线画在 E4 处，而 music21 的
  unpitched 默认是 B4，会浮在线上方 4 个音高）
- **每个音符都是短符头 + 显式休止符**：一次拍手画成一个短符头（默认 8 分音符，
  由 `--score-note-div` 调整），到下一次命中之间的空白用**休止符**补齐——不是
  把音符时值拉长到下一次命中。所以长音在谱面上读作「一个短符头 + 一串休止符」
- **符干一律朝上**（音杠在上、符头在下）
- **轨道前不显示乐器名**（`<part-name>` 为空）
- **标题区**：主标题 = 曲名（`&title`），副标题 = `Lv.13.7 ReMaster`
  （`&lv_N` + 难度名，前等级后等级名）；谱作者（`&des_N`）在左下、曲作者
  （`&artist`）在右下
- `&first` 的前导空白写成起始休止符；末小节不足处补休止符，保证每小节完整
- BPM 变化写成 `MetronomeMark`（`(bpm)` 与变速段都会体现）
- 多个音符同时命中（EACH 等）在谱面上合并为一个符头——一次拍手就是一个符头

### 标题区为什么是自己画进 SVG 的

verovio 用自己的内嵌字体渲染文本，**没有 CJK 字形**——日文曲名会画成豆腐块
（`アンビバレンス` → `▯▯▯▯▯▯▯`）。所以标题区不交给 verovio，而是由
`score_writer.inject_header()` 直接写进页面 SVG，交给 cairosvg 用系统字体渲染：

- 把根 `<svg>` 的 viewBox `minY` 设为负值（`viewBox="0 -260 2100 3230"`），
  原有内容整体下移，负坐标区就成为标题区；页面高度同步增加避免裁切
- 文本必须插在**根** `<svg>` 结束前——verovio 内部还有一个
  `<svg class="definition-scale" viewBox="0 0 21000 29700">` 的嵌套元素
  （10 倍坐标），插进那里面会被缩成 1/10，文字极小且位置错乱
- 每页都预留同样的顶部空间（各页尺寸一致），标题块只画在第 1 页
- MusicXML 里同样写了这些信息（`<work-title>` / `<movement-title>` /
  `<creator type="composer">` / `<creator type="lyricist">`），方便导入
  MuseScore 等软件

### 播放音色（打击乐映射）

显示音位只决定**记谱**，播放音色由 MusicXML 的打击乐声明决定：

```xml
<score-instrument id="I…"><instrument-name>Hand Clap</instrument-name></score-instrument>
<midi-instrument id="I…">
  <midi-channel>10</midi-channel>      <!-- GM 打击乐通道 -->
  <midi-unpitched>40</midi-unpitched>  <!-- 1 基；40 → MIDI 39 = GM Hand Clap -->
</midi-instrument>
```

两者都缺时，播放器无从得知这是打击乐，会退回「按显示音高当旋律乐器弹」——
听起来又低又没有打击感。这两项与显示音位是**解耦**的，所以符头既能贴在线上、
又发出拍手声。

单线谱那条线的位置由 verovio 固定画在 **E4**，无法用 MusicXML 的 `<line>` 或
`<clef-octave-change>` 移动（实测 8 种组合对线位置全部无效）。所以「只改线的
音高标准、让音符保持原音高」在 MusicXML 层面做不到——替代方案就是上面的解耦。

### 与 MuseScore 导出对齐的结构元素

对照 MuseScore 4 从 MIDI 导出的 MusicXML（`score-partwise version="4.0"`），
music21 会漏掉几个导入器期望的元素，`normalise_musicxml()` 在写出后统一补齐：

| 元素 | music21 的行为 | 补齐做法 |
| --- | --- | --- |
| `<part-name>` / `<part-abbreviation>` | 不带 `print-object` | 加 `print-object="no"`：隐藏但仍保留语义 |
| `<note>` 里的 `<voice>` | 只在显式多声部时才写 | 每个 `<note>` 补 `<voice>1</voice>` |
| `<note>` 里的 `<instrument id="…"/>` | 不写 | unpitched 音符补上，指向 `score-instrument` |
| `<midi-instrument>` 的 `<volume>` / `<pan>` | 源码里还是 TODO | 由 velocity 换算（`velocity/127×100`，与 MuseScore 一致） |
| `<key><fifths>0</fifths></key>` | 不写 | 插入 `KeySignature(0)` |
| `<midi-program>` | 为 `None` 时不写 | 设 `midiProgram = 0`（输出 1） |

有意**不**照搬的几项：MuseScore 的 `<measure-style><multiple-rest>`（把连续
空小节折叠成多小节休止）、每个系统一个的 `<print>` 布局信息、以及它把乐器名当
`<credit>` 打印——本工具的标题区是自己注入 SVG 的。

### 连音整拍成组（`renotate_beats`）

music21 的 `makeTupletBrackets` 是**逐个音符**贴连音记号的，于是连音组会被非连音
元素切断，产出退化的「1 个音的连音组」。MuseScore 按记谱值重算小节长度
（`type × 附点 × 连音比`）就会报「不完整小节」，并把整个小节的内容挪位——实测把它
从本工具导出的 MusicXML 再导出的 MIDI 与标准对照，只有 **66.7%** 的拍点正确，
偏差自第一个非法组所在小节（PANDORA ReMaster 第 53 小节）开始。

`renotate_beats()` 在 XML 层按**拍**整组重写：一拍内所有元素（音符与休止符都算）
的时值若都是标准二进制值，就写二进制时值；否则若每个元素的「名义时值」
（时值 × 3/2）都是标准记谱值，就整拍写成 **3:2 连音组**，组首 `start`、组尾 `stop`。
这样「3」的轮廓保留下来，同时不会出现非法分组。

> 注意：只删掉 `<tuplet>` 标记**不能**解决问题——MuseScore 会按
> `<time-modification>` 自行推导分组，一样推不出来。必须让分组本身合法。

验证方式有两个（都不能只看 verovio 渲染，它容忍非法标记）：`validate_all_scores.py`
第 7 项按 MuseScore 的算法重算小节长度；`check_tuplet_groups.py` 要求
「带 `<time-modification>`」⇔「落在 start..stop 括号内」。
`--score-binary` 是逃生方案（完全不产生连音），代价是三连音被展开。

**最终验证记录（由使用者实测确认）**：MuseScore 4 打开导出的 MusicXML **无任何
报错**，且由它再导出的音频与标准 MIDI **完全一致**（修复前是 66.7% 拍点相符）。
45 份全量：七项不变量不合格 **0**、连音组完整性 **0/45**、谱面拍点与 MIDI 位置
一致率 **100%**（≤1 tick）、单元测试与 MIDI 回读全绿。

> 判据的关键在于「按拍整组」而非「按音符」：music21 逐个音符贴标记，所以组会被
> 非连音元素切断；只有整拍判定才能同时满足「合法」与「保留 3 的轮廓」。

### 连结音与符尾：为什么要绕开 music21 的导出器

打击乐谱面「一个符头 = 一记」，出现连结音既难读又误导。但 music21 有两处会
自作主张：

1. `Score.write("musicxml")` 内部**又跑一次** `makeNotation`
   （`m21ToXml.GeneralObjectExporter.fromScore`），把 `merge_tied_notes()`
   刚合回去的连结音重新拆开，谱面上于是又冒出假符头。改用
   `GeneralObjectExporter` 并把 `makeNotation` 置 `False`——music21 官方支持
   这个模式，只接受已经记谱好的 `Score`。
2. 该模式下导出器不再替我们收拾「无法用单个音符或休止符表示的时值」，会直接抛
   `Cannot convert complex durations`。所以在**构造阶段**就把休止符拆成可单值
   记谱的片段（`decompose_ticks()`），并优先只用二进制时值——否则二进制谱面会
   被休止符凭空引入三连音记号；`makeNotation` 之后再用 `split_complex_rests()`
   兜住它合并出来的 1.25 拍这类残留。

实测 PANDORA PARADOXXX ReMaster：改动前 1108 个符头（真实拍点 1095）外加 19 组
连结音；改动后 **1095 个符头、0 个连结音**。

另外**不要**在合并之后重跑 `makeBeams()`：实测它会把小节内容挖出空洞（小节 3
丢掉 1/6 拍）并让 verovio 报 `Unknown dur`。合并会删掉承载 `beam begin/end` 的
符头，符尾配对改由 `balance_beams()` 在写出的 XML 上修——按「遇到 `end` 收一段」
切段重写，幂等；`forward hook` / `backward hook` 是自闭合的，原样保留。

**导出器自己还会改谱面。** `GeneralObjectExporter.parse()` 在写 XML 的过程中会再
碰一次谱面结构：实测内存里读每个小节都是满的，写出来某个小节却少了 1/6 拍
（`<duration>` 加起来不到一小节），而且这一步受进程级哈希随机影响——同一份谱面
不同进程结果不同。上游不受控，所以 `pad_short_measures()` 在**写出的 XML 上**逐
小节核对时间账，差多少补一个 `<forward>`（时间前进但不发声，语义正是缺掉的那个
休止符，music21 自己也这么用）。45 份样张实测：修复前 14 份有残小节，修复后
`PYTHONHASHSEED=0/3/11` 三种子均为 0。

连音括号同理：合并连结音会带走某个 `tuplet` 的 `stop`，`balance_tuplets()` 只删
「没有 `start` 的 `stop`」（纯噪声），悬空的 `start` 保留——verovio 容忍它，谱面
上那个「3」还在。

### 速度标记的音符符号

verovio 把速度标记里的音符符号写成 `<tspan font-family="Leipzig">私用区码位</tspan>`
（**不是**内嵌字形路径）。verovio 其实已经把字体以 base64 `@font-face` 内嵌进
SVG，但 **cairosvg 不支持 `@font-face`**，取不到 Leipzig，于是画成方框
（`♩ = 187` 变成 `□ = 187`）。

`patch_music_font_text()` 把这些私用区码位映射成系统字体里真实存在的 Unicode
音符字符（`♩` U+2669、`♪` U+266A、`♬` U+266C），并沿用原字号缩放
（文本字体的音符字符比 Leipzig 字形大，缩到 0.7 倍才与相邻数字协调）。
实测 SMuFL 的 `U+1D15F` 一类码位系统字体里没有，仍是方框，所以不用它们。

### 量化网格（`--score-grid`，默认 32）

滑星的**经过点**（连结 SLIDE 的中间键、大 V 的 via）时刻是按**路径长度比例**
定位的（`simai_parser.py` 里 `(cum_len + L*frac)/total_len * total_dur`），
它描述的是「手扫到这个位置」的物理时刻，天然不在任何音乐网格上。

直接用这些时刻会得到 `41/480` 拍这类无法记谱的时值，music21 只能用
「2048 分音符连音」去凑，写出 MusicXML 时直接抛
`MusicXMLExportException: Cannot convert "2048th" duration`。

所以乐谱层会先把 tick 吸附到 `--score-grid` 网格并合并重合点。

**默认取 192 分（10 tick），而不是 64/32 分**，因为 maimai 的 `{div}` 里混着
三连音系的分母（本谱就用到 `{12}`/`{24}`/`{48}`/`{192}`）。**192 = 64 × 3** 是
二进制与三连音的最小公倍网格——只有它能同时精确表示 32 分音符（60 tk）与
三连 16 分（80 tk）。实测 `PANDORA PARADOXXX` ReMaster 的 **1095** 个拍点：

| 网格 | 精确落点 | 保留拍点 | 最大误差 |
| --- | --- | --- | --- |
| `32`（60 tk） | 72.6% | 984 | 30 tk |
| `64`（30 tk） | 77.3% | 1083 | 10 tk |
| `96`（20 tk） | 94.9% | 1083 | 10 tk |
| **`192`（10 tk，默认）** | **99.5%** | **1095** | **1 tk** |

二进制网格会把三连音音符挤到邻居上（本谱 244 处 ±10 tk），既丢拍点又**写错
时值**；192 网格让它们落回原样，由 music21 写成真正的**连音记号**（以 3:2
为主，本谱 405 处）。这与 MuseScore 从同一 MIDI 导出的做法一致——参照文件里
也是 3:2 × 280，外加少数非标准比率（7:8 × 16）；本工具同理会产生少量
6:5 / 12:7（来自滑星经过点的几何时刻）。连音在 verovio 里正常渲染为
`3` 连音括号。

```bash
# 更简洁的谱面（16 分网格）
python simai2midi.py maidata.txt --all -O out/ --pdf --score-grid 16

# 3/4 拍的谱面
python simai2midi.py maidata.txt --all -O out/ --musicxml --time-sig 3/4
```

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
python test_simai_parser.py                          # 29 项解析器测试（用例取自官方文档）
python test_midi_writer.py                           # 10 项写出测试（含非 ASCII 曲名、尾判开关与原子保存）
python test_score_writer.py                          # 35 项乐谱测试（单线谱/符头音位/打击乐播放映射/短符头+休止符/标题与音乐字体注入、网格量化回归、连结音与符尾配对、不可记谱时值、MusicXML/PDF 渲染）
python verify_midi.py <maidata.txt> <难度> <out.mid>  # 单文件回读比对
python verify_all.py                                 # 批量回读 samples/ 与 out/
```

`verify_*.py` 会重新解析源谱，把 MIDI 里读回的 note_on 绝对秒数与解析
结果逐一比对，同时检查通道与音色。

实测结果（`verify_all.py`，41 个谱面 / 0 失败 / 0 解析警告）：

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
- `score_writer.py` — 节奏谱：music21 构建乐谱、MusicXML 写出、verovio+cairosvg 渲染 PDF
- `simai2midi.py` — CLI 入口
- `test_simai_parser.py` / `test_midi_writer.py` / `test_score_writer.py` /
  `verify_midi.py` / `verify_all.py` — 测试与校验
- `samples/` — 测试用谱面；`out/` — 生成的 MIDI 与乐谱
