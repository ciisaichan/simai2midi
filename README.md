# simai2midi — maimai 谱面转打击乐节奏谱

English see [README.en.md](README.en.md)

把 **maimai 的 simai 谱面**转换成三样东西：

1. **打击乐 MIDI** —— 每一记对应一次拍手（GM 打击乐通道 9 / Hand Clap 39），
   可直接当节奏参考轨、练习素材，或喂给 DAW 与制谱软件
2. **MusicXML 节奏谱** —— 打击乐单线谱，一个符头 = 一记，**MuseScore 可直接打开**
3. **PDF 节奏谱** —— 由 MusicXML 排版渲染，适合打印或分享

[![演示视频](docs/demo-poster.png)](https://github.com/ciisaichan/simai2midi/releases/download/v1.0.0/demo.mp4)

▶ **[观看演示视频（demo.mp4，31 MB）](https://github.com/ciisaichan/simai2midi/releases/download/v1.0.0/demo.mp4)**

*インターネットサバイバー* Master ｜ 56 秒 ｜ 1920×1080 / 60fps


#### 演示视频素材来源

| 画面位置 | 内容 | 来源 |
| --- | --- | --- |
| 左侧 | 手元（实机游玩） | [bilibili BV1qjztYVE4W](https://www.bilibili.com/video/BV1qjztYVE4W) |
| 右上 | 游戏谱面 | [bilibili BV1kU421d74K](https://www.bilibili.com/video/BV1kU421d74K/) |
| 右下 | 节奏谱（本项目生成的 MIDI） | 用 **MuseScore 4** 播放 |

画面素材版权归原作者与 SEGA 所有，此处仅用于功能演示；音频已做衰减处理。

## 特性

- **纯 Python**，无编译步骤；只要导 MIDI 的话仅需 `mido` 一个依赖
- **完整 simai 解析**：TAP / HOLD / SLIDE / TOUCH / BREAK / EX，含扫键（`- > < ^ v p q s z pp qq V w`）、
  `{div}` 分音、`(bpm)` 变速、`{#秒}` 时长、EACH / 伪 EACH、`&first` 起始偏移
- **正确的时间轴**：支持谱面中途变速。秒 → tick 做分段线性映射，
  变速之后的时间仍然是绝对对齐的
- **可选的密集度控制**：尾判音（`--tails`）、滑星沿途展开（`--slide-dense`）、
  连续音符重采样（`--resample 32/16`）
- **能打开的乐谱**：按**拍**整组生成合法连音组（保留「3」的轮廓，又不会让
  MuseScore 报「不完整小节」）。这是本项目最花力气的地方，细节见
  [技术笔记](docs/README.original.md)
- **自带校验**：回读 MIDI 与源谱逐音比对；乐谱有七项结构不变量检查

## 安装

需要 **Python 3.9 或更新**。

```bash
git clone https://github.com/ciisaichan/simai2midi.git
cd simai2midi

pip install -r requirements.txt          # 只导 MIDI：仅需 mido
pip install -r requirements-score.txt    # 还要导出 MusicXML / PDF
```

macOS 上 `cairosvg` 需要系统 libcairo：

```bash
brew install cairo
```

## 快速开始

```bash
# 默认导出文件内最高难度
python simai2midi.py maidata.txt -o out.mid

# 指定难度槽位（1=Easy 2=Basic 3=Advanced 4=Expert 5=Master 6=ReMaster 7=UTAGE）
python simai2midi.py maidata.txt -d 6 -o remaster.mid

# 一次导出全部难度（按 曲名_难度.mid 命名）
python simai2midi.py maidata.txt --all -O out/

# 连乐谱一起导出
python simai2midi.py maidata.txt --all -O out/ --musicxml --pdf

# 裸 inote 正文（没有 & 头部），手动给 BPM 与起始偏移
python simai2midi.py inote.simai --bpm 174 --first 1.234 -o out.mid

# 更密集的滚奏：滑星沿途每个按钮都算一记
python simai2midi.py maidata.txt -d 6 --slide-dense

# 把 32 分及更密的连续音符重采样到 16 分
python simai2midi.py maidata.txt -d 6 --resample 32/16
```

`input` 可以是 `maidata.txt`（含 `&inote_x=` 段落），也可以是只有谱面正文的裸文本文件。

## 命令行参数

| 参数 | 说明 |
| --- | --- |
| `input` | `maidata.txt` 或裸 inote 正文文件 |
| `-o, --output PATH` | 输出 `.mid` 路径（单难度时有效） |
| `-O, --outdir DIR` | 批量输出目录 |
| `-d, --difficulty N` | 难度槽位 1–7，可重复；默认取最高难度 |
| `--all` | 导出文件内全部难度 |
| `--bpm N` | 正文未写 `(bpm)` 时的默认 BPM（默认读 `&wholebpm`，否则 120） |
| `--first SEC` / `--no-offset` | 覆盖 / 忽略 `&first` 起始偏移 |
| `--distinct` | 按音符类型分配不同打击音色，便于在 DAW 里区分 |
| `--hold-sustain` | HOLD / TOUCH HOLD 按真实时长持续（默认短促一击） |
| `--slide-dense` | 滑星弧线（`> < ^`）展开沿途每个按钮（更密集的滚奏） |
| `--tails` | 包含 HOLD / SLIDE 的尾判音（默认不含） |
| `--resample S/T` | 把 S 分音及更密集的连续音符重采样到 T 分音（如 `32/16`；默认不处理） |
| `--musicxml` | 同时导出节奏谱 MusicXML（`*.musicxml`） |
| `--pdf` | 同时导出节奏谱 PDF（`*.pdf`，由 MusicXML 渲染） |
| `--time-sig N/D` | 乐谱拍号（默认 `4/4`） |
| `--score-grid N` | 乐谱量化网格，N 为分音分母（默认 `192`=10 tick；能同时精确表示 32 分与三连 16 分。改成 `64` 会写错三连音时值） |
| `--score-note-div N` | 谱面短符头时值，N 为分音分母（默认 `8`=八分音符；`16` 更短、休止符更多） |
| `--score-binary` | 逃生通道：乐谱完全不写连音，拍点吸附到 128 分网格。代价是三连音被展开成等值音符 |
| `--velocity V` / `--break-velocity V` | 普通 / BREAK 音符力度（默认 100 / 127） |
| `-q, --quiet` | 只输出结果路径 |

### 节奏命中（拍手点）与尾判

解析器把每个音符展开成若干「拍手点」：

- TAP / TOUCH：1 点（头）
- HOLD / TOUCH HOLD：1 点（头）；加 `--tails` 时再加尾判（结束时刻）
- SLIDE：头 + 经过键（连结 SLIDE 的中间键、大 V 的 via）；加 `--tails` 时再加终点尾判

默认（不含尾判）导出的是一份**纯打击节奏**——与游戏判定里每个需要按下的时刻对应。

### DISTINCT 音色映射

加 `--distinct` 后按音符类型分轨到不同打击乐器，方便在 DAW 里一眼区分：

| 音符 | 打击乐 | MIDI 音高 |
| --- | --- | --- |
| TAP | Hand Clap | 39 |
| HOLD | Cowbell | 56 |
| SLIDE | Tambourine | 54 |
| TOUCH | Closed Hi-Hat | 42 |
| TOUCH HOLD | Open Hi-Hat | 46 |
| BREAK | Crash Cymbal 1 | 49 |

不加时全部用 Hand Clap（39）。

### 扫键重采样（`--resample S/T`）

maimai 里的「扫键」是一串快速连续音符（32 分、64 分等），实际演奏时是**扫**过
而非逐个敲击。`--resample S/T` 把「S 分音及更密」的连续音符重采样到 T 分音：
密集段内按 T 分网格（相对段首）每隔一段保留一记。

- S 分音符时长 = `240/(BPM×S)` 秒；相邻音符间隔不超过该时长即视为同一密集段
- BPM 变化时按当前位置的 BPM 计算阈值；需满足 `0 < T < S`
- 默认不启用

```bash
python simai2midi.py maidata.txt --all -O out/ --resample 32/16
python simai2midi.py maidata.txt --all -O out/ --resample 64/16   # 更激进
```

## 输出文件

以 `maidata.txt` + `-d 6` 为例，产物同名不同后缀：

```
maidata.mid          # 拍手节奏 MIDI（所有模式都会生成）
maidata.musicxml     # 打击乐单线谱，MuseScore 可直接打开
maidata.pdf          # 排版好的 PDF 乐谱
```

用 `-O out/` 批量导出时，文件名取自 `&title` 与难度名，例如
`インターネットサバイバー_Master.mid`。`--pdf` 单独使用时 MusicXML 只作
临时中间文件，用完即删。

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
- 伪 EACH：`` 1`2`3/4 ``（每级延后 1ms）
- 星形抑制 `?` / `!`、星形转普通 TAP `@`
- 终止符 `E`、`||` 行注释、正文内换行与空白

## 常见问题

**Q：MuseScore 打开 MusicXML 报「不完整小节」？**
默认路径已经不会了。这个报错的根源是 music21 生成的连音记谱是「补丁式」的
（连音组被非连音元素切断），MuseScore 按记谱值重算小节长度时就会算错并把
整个小节挪位——实测经它再导出的 MIDI 只有 66.7% 的拍点正确。
本项目在按**拍**整组重写记谱之后，MuseScore 4 打开无报错、其导出的音频与
MIDI 完全一致。极端谱面若仍有提示，可退到 `--score-binary`（完全不产生连音，
代价是三连音被展开成等值音符）。

**Q：报 `no library called "cairo-2" was found`？**
`cairosvg` 需要系统 libcairo：macOS `brew install cairo`，
Debian/Ubuntu `apt install libcairo2`。装好后仍报错时，确认
`/opt/homebrew/lib`（macOS ARM）在 `DYLD_FALLBACK_LIBRARY_PATH` 里。

**Q：只想要 MIDI，不想装一堆乐谱依赖？**
不传 `--musicxml` / `--pdf` 就不会导入 `music21` 等库——乐谱相关的 import
全部延迟到调用时，装 `requirements.txt`（仅 `mido`）即可。

**Q：日文 / 中文曲名会乱码吗？**
不会。`midi_writer.save_midi()` 会显式设置 mido 的 `charset='utf-8'`
（模块级设置无效，必须设在 `MidiFile` 实例上），并用「临时文件 + `os.replace`」
原子写出，避免编码异常留下损坏的半截 `.mid`。

**Q：哪里下载谱面？**
本项目**不附带任何谱面数据**（见下方版权说明）。谱面可从
[adxdls.saop.cc](https://adxdls.saop.cc/charts) 等社区镜像获取；
simai 语法参考 [simai wiki](https://w.atwiki.jp/simai/pages/1002.html)。

## 项目结构

```
simai2midi.py        # 命令行入口
simai_parser.py      # simai 语法解析 → 带绝对秒数的音符对象
slide_geometry.py    # 滑星几何：按钮位置、弧线方向、路径长度
midi_writer.py       # 音符 → 打击乐 MIDI（TickMap 秒→tick 分段线性映射）
score_writer.py      # 音符 → MusicXML → verovio SVG → cairosvg PDF
verify_midi.py       # 单文件回读比对
verify_all.py        # 批量回读比对
test_*.py            # 单元测试（直接 python test_xxx.py 运行，无需 pytest）
docs/                # 演示视频、技术笔记、原版 README 备份
```

## 测试

仓库不附带谱面样本，自测请准备自己的 `maidata.txt`：

```bash
python test_simai_parser.py      # 解析器
python test_midi_writer.py       # MIDI 写出（含非 ASCII 曲名、尾判开关、原子保存）
python test_score_writer.py      # 乐谱记谱
python verify_midi.py maidata.txt 6 out.mid   # 单文件回读比对
python verify_all.py             # 批量回读比对
```

`verify_*.py` 会重新解析源谱，把 MIDI 里读回的 note_on 绝对秒数与解析结果逐一
比对，同时检查通道与音色。误差来源是 480 tick/拍 的量化，实测最大 **0.6 ms**
（45 份谱面全量校验 0 失败），远低于任何听感阈值。

## 已知限制

- `--score-binary` 逃生通道在个别谱面上会出现小节时值溢出（例如 PANDORA
  PARADOXXX 的 50/51 小节）。默认路径不受影响，此选项仅为兼容性兜底。
- 乐谱默认量化到 192 分网格（10 tick）。改用 `--score-grid 64` 会让三连音系
  时值写错，除非你确定该谱面没有三连音分工。
- PDF 排版使用 verovio 默认布局，标题区由本项目自己注入 SVG（verovio 内嵌
  字体没有 CJK 字形）。

## 技术笔记

实现细节（music21 导出器的坑、连音整拍成组、verovio/cairosvg 的渲染问题、
量化网格的取舍、各项实测数据）都在
[docs/README.original.md](docs/README.original.md)。

## 致谢与许可

- 本项目代码以 [MIT 许可证](LICENSE) 发布。
- 解析思路参考了 [MajdataView / MajdataViewX](https://github.com/LingFeng-bbben/MajdataView)
  等社区实现。
- 乐谱排版依赖 [verovio](https://www.verovio.org/)（记谱渲染）与
  [music21](https://web.mit.edu/music21/)（MusicXML 生成）。

### 版权说明

**本项目不包含任何 maimai 谱面数据、曲目音频或游戏素材。**
maimai / 舞萌 DX 的曲目、谱面与相关素材版权归 **SEGA** 所有；
请勿把谱面数据或游戏音频提交进本仓库。演示视频中的曲目仅用于功能演示，
音频已做衰减处理。
