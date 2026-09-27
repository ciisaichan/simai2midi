# simai2midi v1.0.0

首个公开版本：把 maimai（舞萌 DX）的 **simai 谱面**转换为打击乐 MIDI，
并可附带导出 MusicXML / PDF 节奏谱。

## 演示视频

本 Release 的附件 `demo.mp4` 是《インターネットサバイバー》Master 的演示：

| 画面位置 | 内容 | 来源 |
| --- | --- | --- |
| 左侧 | 手元（实机游玩） | https://www.bilibili.com/video/BV1qjztYVE4W |
| 右上 | 游戏谱面 | https://www.bilibili.com/video/BV1kU421d74K/ |
| 右下 | 节奏谱（本项目生成的 MIDI） | 用 MuseScore 4 播放 |

画面素材版权归原作者与 SEGA 所有，此处仅用于功能演示；音频已做衰减处理。

## 附件

| 文件 | 说明 |
| --- | --- |
| `demo.mp4` | 演示视频，1920×1080 / 60fps / 56 秒 / 31 MB（音频已衰减 −10 dB 并留出峰值余量） |

## 本版本要点

- **完整 simai 解析**：TAP / HOLD / SLIDE / TOUCH / BREAK / EX，含全部滑星形状与
  扫键、`{div}` 分音、`(bpm)` 变速、`{#秒}` 时长、EACH / 伪 EACH、`&first` 偏移
- **正确的时间轴**：秒 → tick 分段线性映射，谱面中途变速后仍绝对对齐
- **可选的密集度控制**：尾判音（`--tails`）、滑星沿途展开（`--slide-dense`）、
  连续音符重采样（`--resample 32/16`）、`--distinct` 按音符类型分轨
- **能打开的乐谱**：按**拍**整组重写记谱，生成合法连音组——保留连音「3」的轮廓，
  同时 MuseScore 4 打开无报错，其导出的音频与本工具生成的 MIDI 完全一致
- **自带校验**：MIDI 回读与源谱逐音比对，45 份谱面全量 0 失败，最大误差 0.6 ms

## 安装

```bash
pip install -r requirements.txt          # 只导 MIDI：仅需 mido
pip install -r requirements-score.txt    # 还要导出 MusicXML / PDF
```

macOS 上 `cairosvg` 另需系统 libcairo：`brew install cairo`。

用法与完整参数说明见 [README](../README.md)（英文：[README.en.md](../README.en.md)）。

## 版权说明

本项目**不包含任何 maimai 谱面数据、曲目音频或游戏素材**。
maimai / 舞萌 DX 的曲目、谱面与相关素材版权归 **SEGA** 所有。
