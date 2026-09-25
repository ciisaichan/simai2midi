"""simai slide 几何：按钮位置、弧线方向、路径长度。

几何数据取自 MajdataViewX（MajGeo.cs / SlideTableNeo.cs）：
- 按钮 N (1..8) 在半径 MainRadius 的圆上，角度 θ = π(5/8 - N/4)。
- 外圈弧（> < ^）半径 = 按钮环半径（GetCircle(9) = MainRadius），
  因此弧线会穿过沿途每个按钮。
- 按钮号 1→2→…→8 = 角度递减 = 顺时针；1→8→7→… = 逆时针。
"""
import math

MAIN_RADIUS = 1.0  # 归一化，仅用于求比例


def btn_angle(n: int) -> float:
    return math.pi * (5.0 / 8.0 - n / 4.0)


def btn_pos(n: int) -> tuple:
    a = btn_angle(n)
    return (math.cos(a), math.sin(a))


def chord_len(a: int, b: int) -> float:
    xa, ya = btn_pos(a)
    xb, yb = btn_pos(b)
    return math.hypot(xb - xa, yb - ya)


def arc_nodes(start: int, end: int, cw: bool) -> list:
    """沿按钮环从 start 走到 end（含起终点）。cw=True 顺时针 = 按钮号递增。"""
    nodes = []
    cur = start
    while True:
        nodes.append(cur)
        if cur == end:
            break
        cur = (cur % 8) + 1 if cw else ((cur - 2) % 8) + 1
    return nodes


def arc_dir(start: int, shape: str) -> bool:
    """返回 '>' / '<' 弧线是否顺时针。

    对应 SlideTableNeo：CCW 路径分配给「底部键 >」「顶部键 <」，
    CW 路径分配给「顶部键 >」「底部键 <」。
    """
    bottom = start in (3, 4, 5, 6)
    if shape == ">":
        return not bottom
    if shape == "<":
        return bottom
    raise ValueError(f"非弧线形状: {shape}")


def arc_nodes_of(start: int, end: int, shape: str) -> list:
    """返回弧线（> < ^）经过的按钮（含起终点）。^ 自动取短弧。"""
    if shape == "^":
        cw = arc_nodes(start, end, True)
        ccw = arc_nodes(start, end, False)
        return cw if len(cw) < len(ccw) else ccw
    return arc_nodes(start, end, arc_dir(start, shape))


def segment_len(shape: str, start: int, end: int, via: int | None = None) -> float:
    """一条 slide 段的路径长度（单位圆，仅用于求比例）。"""
    if shape == "-":
        return chord_len(start, end)
    if shape in (">", "<", "^"):
        return (len(arc_nodes_of(start, end, shape)) - 1) * (math.pi / 4.0)
    if shape == "v":            # 经中心折返
        return 2.0
    if shape == "V":            # 大 V：start→via→end 两条直线
        assert via is not None
        return chord_len(start, via) + chord_len(via, end)
    if shape in ("p", "q"):     # 切线 + 中心圆 + 直线（近似）
        return 3.0
    if shape in ("pp", "qq"):   # 更大的圆（近似）
        return 4.7
    if shape in ("s", "z"):     # 折线（近似）
        return 2.6
    if shape == "w":            # wifi：三段并行，这里只算主支
        return chord_len(start, end)
    raise ValueError(f"未知 slide 形状: {shape}")


def segment_hit_nodes(shape: str, start: int, end: int,
                      via: int | None = None, dense: bool = False) -> list:
    """返回该段除起点外的命中点 [(按钮字符串, 段内路径比例)]，按顺序。

    dense=True 时弧线（> < ^）展开沿途每个中间键，形成“滚奏”。
    默认只返回终点；V 返回 via 与终点（via 的比例 = chord(start,via)/总长）。
    """
    if shape in (">", "<", "^") and dense:
        nodes = arc_nodes_of(start, end, shape)
        steps = len(nodes) - 1
        return [(str(n), j / steps) for j, n in enumerate(nodes[1:], start=1)]
    if shape == "V":
        assert via is not None
        a, b = chord_len(start, via), chord_len(via, end)
        total = a + b
        return [(str(via), a / total), (str(end), 1.0)]
    return [(str(end), 1.0)]
