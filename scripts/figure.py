#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""通用可视化引擎（学科无关）：声明式 figure spec -> PNG。

设计目标：**不再按学科枚举图型**。调用方只描述「坐标系 + 数据系列 + 标注」，
引擎负责排版。matplotlib 可用时走它（图型最全）；不可用时退回 Pillow 后端
画基础图元，并把不支持的部分如实列进 unsupported。

spec 结构（除 out 外全部可选）
------------------------------
{
  "out": "a.png", "title": "...", "subtitle": "...", "note": ["底部注 1", "注 2"],
  "width": 1400, "height": 900, "dpi_scale": 2, "backend": "auto|matplotlib|pillow",
  "suptitle_align": "center", "transparent": false,
  "grid": {"rows": 1, "cols": 1, "sharex": true},
  "axes": [
    {
      "slot": [0, 0],                       # [row, col]；省略则依次填充
      "type": "cartesian|logx|logy|loglog|polar",
      "xlim": [0, 10], "ylim": [-1, 1], "xlabel": "x", "ylabel": "y", "unit": {...},
      "title": "子图名", "grid": true, "legend": true, "aspect": "equal|auto",
      "xticks": [0, 2, 4], "yticks": [-1, 0, 1],
      "series": [ ... ]                     # 见 SERIES_KINDS
    }
  ]
}

系列 kind 与字段
----------------
  line/step/scatter/area  : x[], y[]（可省略 x -> 0..n-1）, color, width, style, marker, label
  bar                     : x[], y[], bar_width
  errorbar                : x[], y[], yerr[] 或 xerr[]
  quiver                  : x[], y[], u[], v[]
  contour                 : x[], y[], z[[]], levels, cmap, filled
  heatmap                 : z[[]], x[], y[], cmap, vmin, vmax
  surface3d               : x[], y[], z[[]], cmap, view=[elev, azim]
  function                : expr（变量 x）, xrange=[a,b], n
  parametric              : fx, fy（变量 t）, trange=[a,b], n
  polygon                 : xy[[x,y],...], closed, fill, alpha
  circle / ellipse        : center=[x,y], r 或 width,height,angle
  arrow                   : xy=[x0,y0], xytext=[x1,y1]
  vline / hline           : x 或 y（可为数组）
  text                    : x, y, text, size, color, ha, va, rotation, box
  annotate                : x, y, text, xytext=[..]（省略则自动偏移）, arrow, color, size

所有系列都支持 label（进图例）、color、alpha。
"""

import json
import math
import os
import re
import sys

try:  # stdout 必须是 UTF-8，避免中文在 GBK 控制台下被打碎
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np

# 学科预设只做「学科数据 → 通用系列」的展开，不含任何渲染代码（见 presets.py 顶部说明）。
# 脚本目录在 `python figure.py` 时已经是 sys.path[0]，这里再显式插一次，保证被当模块导入时也能找到。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import presets as preset_registry
except Exception as _preset_error:  # pragma: no cover - 只在文件缺失/损坏时发生
    preset_registry = None
    PRESET_IMPORT_ERROR = str(_preset_error)
else:
    PRESET_IMPORT_ERROR = None

SERIES_KINDS = [
    "line", "step", "scatter", "area", "bar", "errorbar", "quiver",
    # 通用数据图元（学科无关；统计/数据类题图、流程与网络图都用这一组）
    "hist", "stem", "barh", "stacked_bar", "pie", "box", "table", "graph",
    "contour", "heatmap", "surface3d", "function", "parametric",
    "polygon", "circle", "ellipse", "arrow", "vline", "hline", "text", "annotate",
]
# Pillow 后端能画的子集；其余如实进 unsupported
PILLOW_KINDS = {
    "line", "step", "scatter", "area", "bar", "function", "parametric",
    # 通用数据图元：下面这一组两个后端都有实现（见 render_pillow 的系列分支）
    "hist", "stem", "barh", "stacked_bar", "pie", "box", "table", "graph",
    "polygon", "circle", "ellipse", "vline", "hline", "text", "annotate", "arrow",
}
AXES_TYPES = ["cartesian", "logx", "logy", "loglog", "polar"]

DEFAULT_COLORS = ["#2f6fbf", "#e0603c", "#3f9a5a", "#9b59b6", "#d4a017", "#16a2b8", "#8c564b", "#c0392b"]

# 受限表达式求值白名单（本机自用；不是安全沙箱边界）
MATH_NAMESPACE = {
    name: getattr(np, name)
    for name in (
        "sin cos tan arcsin arccos arctan arctan2 sinh cosh tanh exp log log10 log2 sqrt "
        "abs sign floor ceil round power minimum maximum clip where pi e"
    ).split()
}
MATH_NAMESPACE.update({"pi": math.pi, "e": math.e, "x": None, "t": None})


class SpecError(Exception):
    """spec 非法（可读中文原因）。"""


def _require(spec, key, types, what):
    value = spec.get(key)
    if value is None:
        raise SpecError("%s 缺少 %s" % (what, key))
    if types and not isinstance(value, types):
        raise SpecError("%s 的 %s 类型应为 %s" % (what, key, types))
    return value


def _floats(value, what):
    try:
        return [float(v) for v in value]
    except Exception as exc:
        raise SpecError("%s 必须是数值序列（%s）" % (what, exc))


def _matrix(value, what):
    if not isinstance(value, list) or not value:
        raise SpecError("%s 必须是非空二维数组" % what)
    if isinstance(value[0], list):
        return [[float(v) for v in row] for row in value]
    return [float(v) for v in value]


def evaluate(expr, variable, values):
    """受限表达式求值：只暴露白名单函数与变量。

    算符约定：`^` 与 `**` **都表示乘方**。
    真实踩到过：这里只认 Python 的 `**`，于是 `cos(x)^3` 被当成**按位异或**，
    报出 `ufunc 'bitwise_xor' not supported ...` 这种谁也看不懂的 numpy 原生错误；
    而同一个包里的 symbolic.py 是接受 `^` 的——同一套工具两种约定，模型必然写错。
    所以这里把 `^` 归一成 `**`，并且把失败信息说成人话。
    """
    if not isinstance(expr, str) or not expr.strip():
        raise SpecError("表达式不能为空")
    # `^` -> `**`（绘图表达式里 `^` 从来不是异或；`**` 保持原样）
    normalized = re.sub(r"(?<!\*)\^(?!\*)", "**", expr)
    namespace = dict(MATH_NAMESPACE)
    namespace[variable] = np.asarray(values, dtype=float)
    try:
        result = eval(compile(normalized, "<expr>", "eval"), {"__builtins__": {}}, namespace)  # noqa: S307
    except NameError as exc:
        raise SpecError(
            "表达式 %r 里用到了不支持的名称：%s。可用：常数 pi/e，函数 %s"
            % (expr, exc, "、".join(sorted(MATH_NAMESPACE))))
    except SyntaxError as exc:
        raise SpecError("表达式 %r 语法有问题：%s（乘方请写 ^ 或 **）" % (expr, exc))
    except TypeError as exc:
        raise SpecError(
            "表达式 %r 类型不匹配：%s。乘方请写 ^ 或 **（不要用 ^ 以外的位运算写法）" % (expr, exc))
    except Exception as exc:
        raise SpecError("表达式 %r 求值失败：%s" % (expr, exc))
    return np.asarray(result, dtype=float)


# --------------------------------------------------------------------------
# 通用图元的共享几何/统计（学科无关；两个后端调用同一份实现，避免同图不同结果）
# --------------------------------------------------------------------------
PIE_RANGE = 1.6      # pie 的默认数据半宽（数据窗口 [-1.6, 1.6]）
GRAPH_RANGE = 1.6    # graph 自动环形布局时的默认数据半宽


def _rgba(color, alpha=1.0):
    """把 "#rrggbb"/"#rgb" 变成 Pillow 可用的 (r, g, b, a)；解析不了就退回中性灰。"""
    level = max(0, min(255, int(round(255.0 * float(alpha)))))
    try:
        if isinstance(color, str):
            text = color.strip().lstrip("#")
            if len(text) == 3:
                text = "".join(ch * 2 for ch in text)
            if len(text) >= 6:
                return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16), level)
    except Exception:
        pass
    return (120, 120, 120, level)


def series_observations(series, order=("values", "x")):
    """观测值序列：按 order 依次找第一个非空数组（hist 用 values/x，stem 用 values/y）。"""
    for key in order:
        raw = series.get(key)
        if isinstance(raw, list) and raw:
            return _floats(raw, key)
    raise SpecError("%s 需要非空的 %s" % (series.get("kind") or "系列", " 或 ".join(order)))


def histogram_counts(series):
    """hist 的直方图统计：返回 (counts, edges)。两个后端共用同一份数字。"""
    values = np.asarray(series_observations(series, ("values", "x")), dtype=float)
    bins = series.get("bins", 10)
    if isinstance(bins, list) and bins:
        edges = np.asarray([float(v) for v in bins], dtype=float)
        if edges.size < 2:
            raise SpecError("hist 的 bins 至少需要 2 个边界")
        counts, edges = np.histogram(values, bins=edges)
    else:
        try:
            count = int(bins if bins is not None else 10)
        except Exception:
            raise SpecError("hist 的 bins 必须是正整数或升序边界数组")
        if count < 1:
            raise SpecError("hist 的 bins 必须是正整数或升序边界数组")
        counts, edges = np.histogram(values, bins=count)
    if series.get("density"):
        widths = np.diff(edges)
        total = float(values.size) or 1.0
        with np.errstate(divide="ignore", invalid="ignore"):
            counts = np.where(widths > 0, counts / (total * widths), 0.0)
    return [float(c) for c in counts], [float(e) for e in edges]


def box_five(values):
    """一组观测值的五数概括 [min, Q1, 中位数, Q3, max]。"""
    arr = np.asarray(_floats(values, "box"), dtype=float)
    if arr.size == 0:
        raise SpecError("box 的每组数据不能为空")
    q1, med, q3 = np.percentile(arr, [25.0, 50.0, 75.0])
    return [float(arr.min()), float(q1), float(med), float(q3), float(arr.max())]


def box_stats_list(series):
    """box 的每组五数概括：quartiles[[..],..] 直接给，values[[..],..] 现算。"""
    quar = series.get("quartiles")
    if isinstance(quar, list) and quar:
        out = []
        for row in quar:
            if not isinstance(row, list) or len(row) < 5:
                raise SpecError("box 的 quartiles 每项需要 [min, Q1, 中位数, Q3, max] 五个数")
            out.append([float(v) for v in row[:5]])
        return out
    values = series.get("values")
    if not isinstance(values, list) or not values:
        raise SpecError("box 需要 values[[..],..] 或 quartiles[[..],..]")
    if not isinstance(values[0], list):
        values = [values]
    return [box_five(row) for row in values]


def pie_slices(values, startangle=90.0):
    """顺时针饼图扇形：[{lo, hi, frac, value}]，lo/hi 是数学坐标系里的角度区间（逆时针为正）。"""
    if not isinstance(values, list) or not values:
        raise SpecError("pie 需要非空的 values[]")
    vals = [abs(float(v)) for v in values]
    total = sum(vals)
    if total <= 0:
        raise SpecError("pie 的 values 绝对值之和必须大于 0")
    out = []
    angle = float(startangle)
    for value in vals:
        span = 360.0 * value / total
        out.append({"lo": angle - span, "hi": angle, "frac": value / total, "value": value})
        angle -= span
    return out


def graph_layout(series):
    """graph 的节点/边几何：nodes 没给坐标时自动环形布局。返回 (nodes, edges, auto)。"""
    raw = series.get("nodes")
    if not isinstance(raw, list) or not raw:
        raise SpecError("graph 需要非空的 nodes:[{id,x,y,label?}]")
    auto = not any(isinstance(node, dict) and node.get("x") is not None and node.get("y") is not None
                   for node in raw)
    nodes = []
    for index, node in enumerate(raw):
        if not isinstance(node, dict):
            raise SpecError("graph 的 nodes 每一项必须是对象")
        node_id = str(node.get("id", index))
        if auto:
            angle = math.radians(90.0 - 360.0 * index / float(len(raw)))
            x, y = math.cos(angle), math.sin(angle)
        else:
            x, y = float(node.get("x", 0.0)), float(node.get("y", 0.0))
        nodes.append({"id": node_id, "x": x, "y": y,
                      "label": node.get("label", node_id), "r": node.get("r")})
    xs = [node["x"] for node in nodes]
    ys = [node["y"] for node in nodes]
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    default_r = 0.17 if auto else max(span * 0.075, 1e-6)
    for node in nodes:
        node["r"] = float(node["r"]) if node.get("r") is not None else default_r
    by_id = {node["id"]: node for node in nodes}
    edges = []
    for edge in series.get("edges") or []:
        if not isinstance(edge, dict):
            raise SpecError("graph 的 edges 每一项必须是对象")
        head = by_id.get(str(edge.get("from")))
        tail = by_id.get(str(edge.get("to")))
        if head is None or tail is None:
            raise SpecError("graph 的边 %r -> %r 引用了不存在的节点 id"
                            % (edge.get("from"), edge.get("to")))
        edges.append({"a": head, "b": tail, "label": edge.get("label"),
                      "directed": bool(edge.get("directed"))})
    return nodes, edges, auto


def trim_segment(point_from, point_to, radius_from, radius_to):
    """线段两端各缩进一个半径：连线停在圆周上，不穿进节点内部。"""
    dx, dy = point_to[0] - point_from[0], point_to[1] - point_from[1]
    dist = math.hypot(dx, dy)
    if dist <= 1e-12 or dist <= (radius_from + radius_to):
        return point_from, point_to
    ux, uy = dx / dist, dy / dist
    return ((point_from[0] + ux * radius_from, point_from[1] + uy * radius_from),
            (point_to[0] - ux * radius_to, point_to[1] - uy * radius_to))


def arrow_head_points(point_from, point_to, size):
    """有向边的箭头三角形（数据坐标；两个后端用同一几何，画出来一模一样）。"""
    dx, dy = point_to[0] - point_from[0], point_to[1] - point_from[1]
    dist = math.hypot(dx, dy)
    if dist <= 1e-12:
        return None
    ux, uy = dx / dist, dy / dist
    bx, by = point_to[0] - ux * size, point_to[1] - uy * size
    ox, oy = -uy * size * 0.45, ux * size * 0.45
    return [(point_to[0], point_to[1]), (bx + ox, by + oy), (bx - ox, by - oy)]


def stacked_rows(series):
    """stacked_bar 的 (x[], ys[[]], labels[])。"""
    xv = _floats(_require(series, "x", list, "stacked_bar"), "x")
    rows = series.get("ys")
    if not isinstance(rows, list) or not rows:
        raise SpecError("stacked_bar 需要 ys:[[..],..]")
    out = []
    for row in rows:
        vals = _floats(row, "ys")
        if len(vals) != len(xv):
            raise SpecError("stacked_bar 的每一行长度必须与 x 相同（%d vs %d）" % (len(vals), len(xv)))
        out.append(vals)
    return xv, out, [str(v) for v in (series.get("labels") or [])]


def stack_colors(series, count):
    """堆叠/分组图的逐层配色：colors[] 优先，其次整条 series 的 color，最后引擎默认色板。"""
    given = series.get("colors")
    if isinstance(given, list) and given:
        return [given[i % len(given)] for i in range(count)]
    if series.get("color"):
        return [series["color"]] * count
    return [DEFAULT_COLORS[i % len(DEFAULT_COLORS)] for i in range(count)]


def series_extent(series):
    """单个新图元会撑开的自动值域：只算几何图元（与 matplotlib 自动缩放一致，不算文字）。

    Pillow 后端没有自动缩放，必须靠这里算出与 matplotlib 接近的数据窗口。
    """
    kind = series.get("kind")
    xs, ys = [], []
    if kind == "hist":
        counts, edges = histogram_counts(series)
        xs += [edges[0], edges[-1]]
        ys += [0.0] + list(counts)
    elif kind == "stem":
        obs = series_observations(series, ("values", "y"))
        raw_x = series.get("x")
        if isinstance(raw_x, list) and raw_x and len(raw_x) == len(obs):
            xs += _floats(raw_x, "x")
        else:
            xs += [float(i) for i in range(len(obs))]
        ys += obs + [float(series.get("baseline", 0.0))]
    elif kind == "barh":
        widths = _floats(_require(series, "x", list, "barh"), "x")
        raw_y = series.get("y")
        if isinstance(raw_y, list) and raw_y:
            ys += _floats(raw_y, "y")
        else:
            ys += [float(i) for i in range(len(widths))]
        xs += widths + [0.0]
    elif kind == "stacked_bar":
        xv, rows, _labels = stacked_rows(series)
        xs += xv
        positive = [0.0] * len(xv)
        negative = [0.0] * len(xv)
        for row in rows:
            for index, value in enumerate(row):
                if value >= 0:
                    positive[index] += value
                else:
                    negative[index] += value
        ys += [0.0, max(positive), min(negative)]
    elif kind == "box":
        stats = box_stats_list(series)
        xs += [0.5, len(stats) + 0.5]
        for five in stats:
            ys += five
    elif kind == "pie":
        xs += [-PIE_RANGE, PIE_RANGE]
        ys += [-PIE_RANGE, PIE_RANGE]
    elif kind == "graph":
        nodes, _edges, auto = graph_layout(series)
        if auto:
            xs += [-GRAPH_RANGE, GRAPH_RANGE]
            ys += [-GRAPH_RANGE, GRAPH_RANGE]
        else:
            for node in nodes:
                xs += [node["x"] - node["r"], node["x"] + node["r"]]
                ys += [node["y"] - node["r"], node["y"] + node["r"]]
    return xs, ys


def draw_box_mpl(ax, center, five, width, color, linewidth, alpha):
    """matplotlib 后端的箱线图（手工画：两个后端同一几何，不依赖 boxplot 的版本差异）。"""
    from matplotlib.patches import Polygon as MplPolygon
    low, q1, median, q3, high = five
    half = float(width) / 2.0
    corners = [(center - half, q1), (center + half, q1), (center + half, q3), (center - half, q3)]
    ax.add_patch(MplPolygon(corners, closed=True, facecolor=color, edgecolor="none", alpha=0.22))
    ax.add_patch(MplPolygon(corners, closed=True, facecolor="none", edgecolor=color,
                            linewidth=linewidth, alpha=alpha))
    ax.plot([center - half, center + half], [median, median], color=color,
            linewidth=linewidth * 1.2, alpha=alpha)
    ax.plot([center, center], [q3, high], color=color, linewidth=linewidth * 0.9, alpha=alpha)
    ax.plot([center, center], [low, q1], color=color, linewidth=linewidth * 0.9, alpha=alpha)
    cap = half * 0.55
    ax.plot([center - cap, center + cap], [high, high], color=color,
            linewidth=linewidth * 0.9, alpha=alpha)
    ax.plot([center - cap, center + cap], [low, low], color=color,
            linewidth=linewidth * 0.9, alpha=alpha)


# --------------------------------------------------------------------------
# matplotlib 后端
# --------------------------------------------------------------------------
def render_matplotlib(spec, out, width, height, scale):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import rcParams
    from matplotlib import ticker as mticker

    rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans"]
    rcParams["axes.unicode_minus"] = False   # 负号不画成方框
    rcParams["mathtext.fontset"] = "dejavusans"
    rcParams["figure.autolayout"] = False

    panels = spec.get("axes") or [{"slot": [0, 0]}]
    grid = spec.get("grid") or {}
    rows = int(grid.get("rows") or 1)
    cols = int(grid.get("cols") or 1)
    need = max(rows, cols, len(panels))
    if rows * cols < len(panels):
        cols = len(panels)
        rows = 1
    dpi = 110.0
    fig = plt.figure(figsize=(width / dpi, height / dpi), dpi=dpi * scale)
    # 约束布局比 tight_layout 更能避免"刻度标签压住轴名"（视觉评审反复报的一类问题）
    constrained = False
    try:
        fig.set_layout_engine("constrained")
        constrained = True
    except Exception:
        constrained = False
    if spec.get("title"):
        fig.suptitle(str(spec["title"]), fontsize=15 * math.sqrt(scale), fontweight="bold")
    if spec.get("subtitle"):
        fig.text(0.5, 0.945, str(spec["subtitle"]), ha="center", fontsize=10, color="#555555")

    unsupported = []
    used = []
    auto_slot = 0
    for index, panel in enumerate(panels):
        kind = panel.get("type") or "cartesian"
        if kind not in AXES_TYPES:
            raise SpecError("未知坐标系 %r（可用 %s）" % (kind, "/".join(AXES_TYPES)))
        slot = panel.get("slot")
        if not slot:
            slot = [auto_slot // cols, auto_slot % cols]
            auto_slot += 1
        projection = "polar" if kind == "polar" else None
        ax = fig.add_subplot(rows, cols, slot[0] * cols + slot[1] + 1, projection=projection)
        if panel.get("title"):
            ax.set_title(str(panel["title"]), fontsize=11)
        if kind == "logx":
            ax.set_xscale("log")
        elif kind == "logy":
            ax.set_yscale("log")
        elif kind == "loglog":
            ax.set_xscale("log")
            ax.set_yscale("log")
        if kind != "polar":
            if panel.get("xlim"):
                ax.set_xlim(*[float(v) for v in panel["xlim"]])
            if panel.get("ylim"):
                ax.set_ylim(*[float(v) for v in panel["ylim"]])
            if panel.get("xticks"):
                ax.set_xticks(_floats(panel["xticks"], "xticks"))
            elif panel.get("type") not in ("logx", "loglog"):
                # 自动刻度取整齐值（避免出现 10.8 这类碎刻度挤在轴名旁边）
                ax.xaxis.set_major_locator(mticker.MaxNLocator(nbins=7, steps=[1, 2, 2.5, 5, 10]))
            if panel.get("yticks"):
                ax.set_yticks(_floats(panel["yticks"], "yticks"))
            elif panel.get("type") not in ("logy", "loglog"):
                ax.yaxis.set_major_locator(mticker.MaxNLocator(nbins=7, steps=[1, 2, 2.5, 5, 10]))
            if panel.get("xlabel"):
                ax.set_xlabel(str(panel["xlabel"]), fontsize=10)
            if panel.get("ylabel"):
                ax.set_ylabel(str(panel["ylabel"]), fontsize=10)
            if panel.get("aspect") == "equal":
                ax.set_aspect("equal", adjustable="box")
            ax.grid(bool(panel.get("grid", True)), alpha=0.25, linewidth=0.6)
            # 多图共享 x 轴时，只让最下面一张显示 x 刻度标签（标准三图/双图排法）
            if panel.get("hide_xticklabels"):
                ax.tick_params(labelbottom=False)
        if panel.get("unit"):
            unit_text = "　".join("%s: %s" % (k, v) for k, v in dict(panel["unit"]).items())
            ax.text(0.995, 1.02, unit_text, transform=ax.transAxes, ha="right", va="bottom",
                    fontsize=8, color="#666666")

        for series in panel.get("series") or []:
            skind = series.get("kind")
            if skind not in SERIES_KINDS:
                raise SpecError("未知系列 kind=%r（可用 %s）" % (skind, "/".join(SERIES_KINDS)))
            color = series.get("color")
            label = series.get("label")
            alpha = float(series.get("alpha", 1.0))
            lw = float(series.get("width", 2.0))
            if skind in ("line", "step", "scatter", "area", "bar", "errorbar", "quiver"):
                xs = series.get("x")
                ys = series.get("y")
                if skind == "quiver":
                    xx = _floats(_require(series, "x", list, "quiver"), "x")
                    yy = _floats(_require(series, "y", list, "quiver"), "y")
                    uu = _floats(_require(series, "u", list, "quiver"), "u")
                    vv = _floats(_require(series, "v", list, "quiver"), "v")
                    ax.quiver(xx, yy, uu, vv, angles="xy", scale_units="xy", scale=1,
                              color=color, alpha=alpha, label=label)
                else:
                    yv = _floats(_require(series, "y", list, "系列"), "y")
                    xv = _floats(xs, "x") if xs else list(range(len(yv)))
                    if len(xv) != len(yv):
                        raise SpecError("x 与 y 长度不一致（%d vs %d）" % (len(xv), len(yv)))
                    if skind == "line":
                        ax.plot(xv, yv, color=color, linewidth=lw, alpha=alpha,
                                linestyle=series.get("style", "-"),
                                marker=series.get("marker"), label=label)
                    elif skind == "step":
                        ax.step(xv, yv, where=series.get("where", "post"), color=color,
                                linewidth=lw, alpha=alpha, label=label)
                    elif skind == "scatter":
                        ax.scatter(xv, yv, color=color, s=float(series.get("size", 28)), alpha=alpha,
                                   marker=series.get("marker", "o"), label=label)
                    elif skind == "area":
                        ax.fill_between(xv, series.get("baseline", 0.0), yv, color=color,
                                        alpha=float(series.get("alpha", 0.3)), label=label)
                    elif skind == "bar":
                        ax.bar(xv, yv, width=float(series.get("bar_width", 0.8)), color=color,
                               alpha=alpha, label=label)
                    else:  # errorbar
                        yerr = series.get("yerr")
                        xerr = series.get("xerr")
                        ax.errorbar(xv, yv, yerr=_floats(yerr, "yerr") if yerr else None,
                                    xerr=_floats(xerr, "xerr") if xerr else None,
                                    color=color, linewidth=lw, capsize=float(series.get("capsize", 3)),
                                    marker=series.get("marker", "o"), alpha=alpha, label=label)
            elif skind in ("contour", "heatmap", "surface3d"):
                z = np.array(_matrix(_require(series, "z", None, skind), "z"), dtype=float)
                cmap = series.get("cmap", "viridis")
                if skind == "surface3d":
                    if panel.get("type") != "surface":
                        pass
                    ax.remove()
                    ax = fig.add_subplot(rows, cols, slot[0] * cols + slot[1] + 1, projection="3d")
                    if panel.get("title"):
                        ax.set_title(str(panel["title"]), fontsize=11)
                    xx = series.get("x")
                    yy = series.get("y")
                    xs = np.array(_floats(xx, "x"), dtype=float) if xx else np.arange(z.shape[1])
                    ys = np.array(_floats(yy, "y"), dtype=float) if yy else np.arange(z.shape[0])
                    gx, gy = np.meshgrid(xs, ys)
                    ax.plot_surface(gx, gy, z, cmap=cmap, linewidth=0, antialiased=True, alpha=alpha)
                    view = series.get("view") or [28, -60]
                    ax.view_init(elev=float(view[0]), azim=float(view[1]))
                    if panel.get("xlabel"):
                        ax.set_xlabel(str(panel["xlabel"]), fontsize=8)
                    if panel.get("ylabel"):
                        ax.set_ylabel(str(panel["ylabel"]), fontsize=8)
                    if panel.get("zlabel"):
                        ax.set_zlabel(str(panel["zlabel"]), fontsize=8)
                elif skind == "contour":
                    xx = series.get("x")
                    yy = series.get("y")
                    xs = np.array(_floats(xx, "x"), dtype=float) if xx else np.arange(z.shape[1])
                    ys = np.array(_floats(yy, "y"), dtype=float) if yy else np.arange(z.shape[0])
                    levels = series.get("levels")
                    if series.get("filled", True):
                        cs = ax.contourf(xs, ys, z, levels=int(levels or 12), cmap=cmap, alpha=alpha)
                    else:
                        cs = ax.contour(xs, ys, z, levels=int(levels or 12), cmap=cmap)
                    if series.get("colorbar", True):
                        fig.colorbar(cs, ax=ax, shrink=0.85)
                else:  # heatmap
                    xx = series.get("x")
                    yy = series.get("y")
                    xs = _floats(xx, "x") if xx else None
                    ys = _floats(yy, "y") if yy else None
                    extent = None
                    if xs and ys:
                        extent = [min(xs), max(xs), min(ys), max(ys)]
                    im = ax.imshow(z, cmap=cmap, origin=series.get("origin", "lower"), aspect="auto",
                                   extent=extent, vmin=series.get("vmin"), vmax=series.get("vmax"),
                                   alpha=alpha)
                    if series.get("colorbar", True):
                        fig.colorbar(im, ax=ax, shrink=0.85)
            elif skind in ("function", "parametric"):
                rng = series.get("trange") if skind == "parametric" else series.get("xrange")
                if not rng:
                    raise SpecError("%s 需要 %s" % (skind, "trange" if skind == "parametric" else "xrange"))
                n = int(series.get("n") or 400)
                t = np.linspace(float(rng[0]), float(rng[1]), n)
                if skind == "function":
                    yv = evaluate(_require(series, "expr", str, "function"), "x", t)
                    ax.plot(t, yv, color=color, linewidth=lw, alpha=alpha,
                            linestyle=series.get("style", "-"), label=label)
                else:
                    xv = evaluate(_require(series, "fx", str, "parametric"), "t", t)
                    yv = evaluate(_require(series, "fy", str, "parametric"), "t", t)
                    ax.plot(xv, yv, color=color, linewidth=lw, alpha=alpha, label=label)
            elif skind == "polygon":
                xy = series.get("xy")
                if not isinstance(xy, list) or len(xy) < 2:
                    raise SpecError("polygon 需要 xy 至少 2 个点")
                pts = [(float(p[0]), float(p[1])) for p in xy]
                patch = plt.Polygon(pts, closed=bool(series.get("closed", True)),
                                    facecolor=series.get("facecolor", "none"),
                                    edgecolor=color or "#333333", linewidth=lw,
                                    alpha=alpha, label=label, hatch=series.get("hatch"))
                ax.add_patch(patch)
            elif skind in ("circle", "ellipse"):
                center = _floats(_require(series, "center", list, "circle/ellipse"), "center")
                if skind == "circle":
                    r = float(_require(series, "r", (int, float), "circle"))
                    patch = plt.Circle(center, r, facecolor=series.get("facecolor", "none"),
                                       edgecolor=color or "#333333", linewidth=lw, alpha=alpha)
                else:
                    w = float(_require(series, "width", (int, float), "ellipse"))
                    h = float(_require(series, "height", (int, float), "ellipse"))
                    patch = plt.Ellipse(center, w, h, angle=float(series.get("angle", 0.0)),
                                        facecolor=series.get("facecolor", "none"),
                                        edgecolor=color or "#333333", linewidth=lw, alpha=alpha)
                ax.add_patch(patch)
                if series.get("equal_aspect", True) and panel.get("aspect") != "auto":
                    ax.set_aspect("equal", adjustable="box")
            elif skind == "arrow":
                xy = _floats(_require(series, "xy", list, "arrow"), "xy")
                target = series.get("xytext")
                if not target:
                    raise SpecError("arrow 需要 xytext 作为箭尾")
                target = _floats(target, "xytext")
                ax.annotate("", xy=xy, xytext=target,
                            arrowprops=dict(arrowstyle=series.get("style", "->"), color=color or "#333333",
                                            linewidth=lw, alpha=alpha))
            elif skind in ("vline", "hline"):
                values = series.get("x") if skind == "vline" else series.get("y")
                if values is None:
                    raise SpecError("%s 需要 %s" % (skind, "x" if skind == "vline" else "y"))
                seq = values if isinstance(values, list) else [values]
                for value in _floats(seq, skind):
                    if skind == "vline":
                        ax.axvline(value, color=color or "#888888", linewidth=lw, alpha=alpha,
                                   linestyle=series.get("style", "--"))
                    else:
                        ax.axhline(value, color=color or "#888888", linewidth=lw, alpha=alpha,
                                   linestyle=series.get("style", "--"))
            elif skind == "text":
                pos = _floats(_require(series, "xy", list, "text"), "xy")
                ax.text(pos[0], pos[1], str(series.get("text", "")), fontsize=float(series.get("size", 10)),
                        color=color or "#222222", ha=series.get("ha", "center"), va=series.get("va", "center"),
                        rotation=float(series.get("rotation", 0.0)), alpha=alpha,
                        bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none", alpha=0.75)
                        if series.get("box", True) else None)
            elif skind == "annotate":
                pos = _floats(_require(series, "xy", list, "annotate"), "annotate")
                # xytext 是相对标注点的**点偏移**（不是数据偏移）：避免把文字丢到坐标范围外、撑大画布
                offset = _floats(series.get("xytext") or [18, 18], "xytext")
                # 引线必须停在文字**外缘**：matplotlib 默认把箭头指向文字中心，
                # 于是虚线会从文字中间穿过去（正是弯矩图极值标注那处缺陷的根因）。
                # 由偏移方向推出文字相对锚点的方位，再用 relpos 把终点贴到那一侧的角上。
                rel_x = 0.0 if offset[0] > 0 else (1.0 if offset[0] < 0 else 0.5)
                rel_y = 0.0 if offset[1] > 0 else (1.0 if offset[1] < 0 else 0.5)
                ha = series.get("ha") or ("left" if offset[0] >= 0 else "right")
                va = series.get("va") or ("bottom" if offset[1] >= 0 else "top")
                ax.annotate(str(series.get("text", "")), xy=(pos[0], pos[1]),
                            xytext=(offset[0], offset[1]), textcoords="offset points",
                            fontsize=float(series.get("size", 10)), color=color or "#c0392b",
                            ha=ha, va=va, annotation_clip=False,
                            arrowprops=dict(arrowstyle="-", color=color or "#c0392b", linewidth=1.0,
                                            linestyle=(0, (4, 3)), shrinkA=1.0, shrinkB=3.0,
                                            relpos=(rel_x, rel_y))
                            if series.get("arrow", True) else None,
                            bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none", alpha=1.0)
                            if series.get("box", True) else None)
            # ---- 通用数据图元（学科无关的追加层；matplotlib 实现） --------------
            elif skind == "hist":
                counts, edges = histogram_counts(series)
                centers = [(edges[i] + edges[i + 1]) / 2.0 for i in range(len(counts))]
                widths = [(edges[i + 1] - edges[i]) * 0.96 for i in range(len(counts))]
                ax.bar(centers, counts, width=widths, color=color or DEFAULT_COLORS[0],
                       edgecolor=series.get("edgecolor", "#ffffff"), linewidth=0.8,
                       alpha=alpha, label=label)
            elif skind == "stem":
                obs = series_observations(series, ("values", "y"))
                raw_x = series.get("x")
                if isinstance(raw_x, list) and raw_x and len(raw_x) == len(obs):
                    xv = _floats(raw_x, "x")
                else:
                    xv = [float(i) for i in range(len(obs))]
                base = float(series.get("baseline", 0.0))
                mark = color or DEFAULT_COLORS[0]
                ax.vlines(xv, base, obs, color=mark, linewidth=lw, alpha=alpha)
                ax.plot(xv, obs, linestyle="none", marker=series.get("marker", "o"),
                        markersize=float(series.get("size", 5.0)), color=mark, alpha=alpha,
                        label=label)
            elif skind == "barh":
                widths = _floats(_require(series, "x", list, "barh"), "x")
                raw_y = series.get("y")
                if isinstance(raw_y, list) and raw_y:
                    yv = _floats(raw_y, "y")
                    if len(yv) != len(widths):
                        raise SpecError("barh 的 x 与 y 长度不一致（%d vs %d）" % (len(widths), len(yv)))
                else:
                    yv = [float(i) for i in range(len(widths))]
                ax.barh(yv, widths, height=float(series.get("bar_height", 0.7)),
                        color=color or DEFAULT_COLORS[0], alpha=alpha, label=label)
            elif skind == "stacked_bar":
                xv, rows, labels = stacked_rows(series)
                palette = stack_colors(series, len(rows))
                bottom = np.zeros(len(xv))
                for index, row in enumerate(rows):
                    ax.bar(xv, row, width=float(series.get("bar_width", 0.8)), bottom=bottom,
                           color=palette[index], alpha=alpha,
                           label=(labels[index] if index < len(labels) else None),
                           edgecolor=series.get("edgecolor", "#ffffff"), linewidth=0.6)
                    bottom = bottom + np.asarray(row, dtype=float)
            elif skind == "box":
                stats = box_stats_list(series)
                width = float(series.get("box_width", 0.55))
                palette = stack_colors(series, len(stats))
                for index, five in enumerate(stats):
                    draw_box_mpl(ax, float(index + 1), five, width, palette[index], lw, alpha)
                labels = series.get("labels") or []
                if labels:
                    # 类别名画在框内下方（两个后端同一位置），不动刻度带，避免与刻度文字打架
                    lows = [five[0] for five in stats]
                    highs = [five[4] for five in stats]
                    span = max(max(highs) - min(lows), 1e-9)
                    label_y = min(lows) - span * 0.025
                    for index in range(min(len(labels), len(stats))):
                        ax.text(float(index + 1), label_y, str(labels[index]), fontsize=8.5,
                                color="#546e7a", ha="center", va="center",
                                bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.75))
            elif skind == "pie":
                from matplotlib.patches import Wedge
                values = _floats(_require(series, "values", list, "pie"), "values")
                labels = series.get("labels") or []
                slices = pie_slices(values, float(series.get("startangle", 90.0)))
                radius = float(series.get("radius", 0.78))
                palette = stack_colors(series, len(slices))
                if not panel.get("xlim"):
                    ax.set_xlim(-PIE_RANGE, PIE_RANGE)
                if not panel.get("ylim"):
                    ax.set_ylim(-PIE_RANGE, PIE_RANGE)
                for index, wedge in enumerate(slices):
                    ax.add_patch(Wedge((0.0, 0.0), radius, wedge["lo"], wedge["hi"],
                                       facecolor=palette[index], edgecolor="white",
                                       linewidth=1.0, alpha=alpha))
                    mid = math.radians((wedge["lo"] + wedge["hi"]) / 2.0)
                    text = str(labels[index]) if index < len(labels) else ""
                    if series.get("show_percent", True):
                        text = ("%s %.0f%%" % (text, wedge["frac"] * 100.0)).strip()
                    ax.text(math.cos(mid) * radius * 1.28, math.sin(mid) * radius * 1.28, text,
                            fontsize=8.5, color="#333333",
                            ha="left" if math.cos(mid) > 0.18 else ("right" if math.cos(mid) < -0.18 else "center"),
                            va="bottom" if math.sin(mid) > 0.18 else ("top" if math.sin(mid) < -0.18 else "center"),
                            bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.8))
                ax.set_aspect("equal", adjustable="box")
                ax.axis("off")
            elif skind == "table":
                columns = [str(c) for c in (series.get("columns") or [])]
                rows = [[("" if v is None else str(v)) for v in row] for row in (series.get("rows") or [])]
                if not columns and not rows:
                    raise SpecError("table 需要 columns[] 或 rows[[..],..]")
                table = ax.table(cellText=rows or None, colLabels=columns or None,
                                 loc="center", cellLoc="center")
                table.auto_set_font_size(False)
                table.set_fontsize(float(series.get("size", 9.0)))
                table.scale(1.0, float(series.get("row_scale", 1.5)))
                ax.axis("off")
            elif skind == "graph":
                nodes, edges, auto = graph_layout(series)
                if auto and not panel.get("xlim"):
                    ax.set_xlim(-GRAPH_RANGE, GRAPH_RANGE)
                if auto and not panel.get("ylim"):
                    ax.set_ylim(-GRAPH_RANGE, GRAPH_RANGE)
                node_face = series.get("node_color", "#e8f0fb")
                edge_color = series.get("edge_color", "#7b8794")
                node_edge = color or DEFAULT_COLORS[0]
                edge_lw = float(series.get("width", 1.6))
                label_size = float(series.get("size", 9.0))
                for edge in edges:
                    p_from, p_to = trim_segment((edge["a"]["x"], edge["a"]["y"]),
                                                (edge["b"]["x"], edge["b"]["y"]),
                                                edge["a"]["r"], edge["b"]["r"])
                    ax.plot([p_from[0], p_to[0]], [p_from[1], p_to[1]], color=edge_color,
                            linewidth=edge_lw, alpha=alpha, zorder=1)
                    if edge["directed"]:
                        head = arrow_head_points(p_from, p_to,
                                                 float(series.get("arrow_size") or 0.0)
                                                 or edge["b"]["r"] * 0.55)
                        if head:
                            ax.add_patch(plt.Polygon(head, closed=True, facecolor=edge_color,
                                                     edgecolor="none", alpha=alpha, zorder=2))
                    if edge.get("label"):
                        mx, my = (p_from[0] + p_to[0]) / 2.0, (p_from[1] + p_to[1]) / 2.0
                        dx, dy = p_to[0] - p_from[0], p_to[1] - p_from[1]
                        dist = math.hypot(dx, dy) or 1.0
                        offset = edge["a"]["r"] * 0.55
                        ax.text(mx - dy / dist * offset, my + dx / dist * offset, str(edge["label"]),
                                fontsize=max(label_size - 1.5, 6.0), color="#54606e",
                                ha="center", va="center", zorder=5,
                                bbox=dict(boxstyle="round,pad=0.18", fc="white", ec="none", alpha=0.85))
                for node in nodes:
                    ax.add_patch(plt.Circle((node["x"], node["y"]), node["r"], facecolor=node_face,
                                            edgecolor=node_edge, linewidth=lw, alpha=alpha, zorder=3))
                    ax.text(node["x"], node["y"], str(node["label"]), fontsize=label_size,
                            color="#22303c", ha="center", va="center", zorder=4)
                if panel.get("aspect") != "auto":
                    # 用 datalim 而不是 box：画框仍占满子图区，只把数据窗口撑成等比例，
                    # 这样节点是正圆、也没有"画框缩小后上方一大片空白"的问题
                    ax.set_aspect("equal", adjustable="datalim")
            used.append(skind)

        handles, labels = ax.get_legend_handles_labels()
        if panel.get("legend", True) and labels:
            ax.legend(fontsize=8, framealpha=0.85, loc=panel.get("legend_loc", "best"))

    notes = spec.get("note") or []
    if notes:
        fig.text(0.012, 0.012, "\n".join("· %s" % n for n in notes[:4]), ha="left", va="bottom",
                 fontsize=8, color="#666666")
    if not constrained:
        # 老版本 matplotlib 没有约束布局时才退回 tight_layout
        fig.tight_layout(rect=(0, 0.02 if notes else 0, 1, 0.94 if spec.get("title") else 1))
    fig.savefig(out, dpi=dpi * scale, facecolor="white",
                transparent=bool(spec.get("transparent")), bbox_inches="tight")
    plt.close(fig)
    return {"backend": "matplotlib", "unsupported": unsupported, "kinds": sorted(set(used)),
            "panels": len(panels)}


# --------------------------------------------------------------------------
# Pillow 后端（基础图元；覆盖面小于 matplotlib，差异如实上报）
# --------------------------------------------------------------------------
def _nice_ticks(lo, hi, target=5):
    """按 1-2-5 序列选"整齐"刻度，并把范围吸附到刻度边界。

    matplotlib 有 MaxNLocator 自动做这件事，Pillow 后端原先只是把范围**等分 5 份**
    再用 %.3g 打印，于是刻度长成 20.1 / 11.3 / -6.32 / 0.888 这种样子，
    而且全正数据也会被画到负值区（实测踩到过）。这里补上这段通用逻辑。
    返回 (lo, hi, ticks)；无法处理时原样返回。
    """
    if not (hi > lo):
        return lo, hi, []
    span = float(hi - lo)
    raw = span / float(max(2, target - 1))
    try:
        magnitude = 10.0 ** math.floor(math.log10(raw))
    except (ValueError, OverflowError):
        return lo, hi, []
    step = magnitude * 10.0
    for multiplier in (1.0, 2.0, 2.5, 5.0, 10.0):
        step = magnitude * multiplier
        if span / step <= target:
            break
    if not (step > 0) or not math.isfinite(step):
        return lo, hi, []
    lo_t = math.floor(lo / step) * step
    hi_t = math.ceil(hi / step) * step
    count = int(round((hi_t - lo_t) / step))
    if count < 1 or count > 40:
        return lo, hi, []
    ticks = [lo_t + index * step for index in range(count + 1)]
    # 消除浮点毛刺（0.30000000000000004 → 0.3）
    ticks = [float("%.12g" % value) for value in ticks]
    return ticks[0], ticks[-1], ticks


def render_pillow(spec, out, width, height, scale):
    from PIL import Image, ImageDraw, ImageFont

    def _pillow_label(draw, xy, text, font, color, scale, anchor):
        """带白底的文字：压在曲线/填充上也能读出来（Pillow 没有 bbox 参数，只能自己铺底色）。"""
        if not text:
            return
        try:
            box = draw.textbbox(xy, text, font=font, anchor=anchor)
        except Exception:
            draw.text(xy, text, font=font, fill=color, anchor=anchor)
            return
        pad = 2 * scale
        draw.rectangle([box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad], fill="white")
        draw.text(xy, text, font=font, fill=color, anchor=anchor)

    panels = spec.get("axes") or [{"slot": [0, 0]}]
    unsupported = []
    kinds = []

    def font(size, bold=False):
        for name in (("msyhbd.ttc" if bold else "msyh.ttc"), "simhei.ttf", "simsun.ttc", "Deng.ttf"):
            path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", name)
            if os.path.exists(path):
                try:
                    return ImageFont.truetype(path, int(size * scale), index=0)
                except Exception:
                    pass
        return ImageFont.load_default()

    W = int(width * scale)
    rows = len(panels)
    head = int(70 * scale) if spec.get("title") else int(24 * scale)
    foot = int(60 * scale) if spec.get("note") else int(30 * scale)
    each = int((height * scale - head - foot) / max(rows, 1))
    img = Image.new("RGB", (W, int(height * scale)), "white")
    draw = ImageDraw.Draw(img, "RGBA")
    f_title = font(22, True)
    f_axis = font(14, True)
    f_tick = font(12)
    f_val = font(13)
    if spec.get("title"):
        draw.text((W / 2.0, 20 * scale), str(spec["title"]), font=f_title, fill="#1a1a1a", anchor="ma")

    for index, panel in enumerate(panels):
        ptype = panel.get("type") or "cartesian"
        if ptype in ("polar",):
            unsupported.append("坐标系 polar（Pillow 后端不支持，请用 matplotlib）")
        elif ptype in ("logx", "logy", "loglog"):
            unsupported.append("坐标系 %s（Pillow 后端按线性轴绘制，请用 matplotlib 得到对数轴）" % ptype)
        if panel.get("xticks") or panel.get("yticks"):
            unsupported.append("自定义 xticks/yticks（Pillow 后端使用均匀刻度）")
        top = head + index * each
        box = (int(88 * scale), top + int(26 * scale), W - int(40 * scale), top + each - int(46 * scale))
        l, t, r_, b = box

        series_list = panel.get("series") or []
        for series in series_list:
            skind = series.get("kind")
            if skind not in PILLOW_KINDS:
                if skind in SERIES_KINDS:
                    unsupported.append("系列 %s（Pillow 后端不支持，请用 matplotlib）" % skind)
                else:
                    raise SpecError("未知系列 kind=%r" % skind)

        # 值域
        xs_all, ys_all = [], []
        for series in series_list:
            skind = series.get("kind")
            if skind in ("line", "step", "scatter", "area", "bar"):
                if series.get("y"):
                    ys_all += _floats(series["y"], "y")
                    xs_all += _floats(series["x"], "x") if series.get("x") else list(range(len(series["y"])))
            elif skind == "function":
                rng = series.get("xrange") or [0, 1]
                grid = np.linspace(float(rng[0]), float(rng[1]), int(series.get("n") or 200))
                xs_all += list(grid)
                ys_all += list(evaluate(series.get("expr"), "x", grid))
            elif skind == "parametric":
                rng = series.get("trange") or [0, 1]
                grid = np.linspace(float(rng[0]), float(rng[1]), int(series.get("n") or 200))
                xs_all += list(evaluate(series.get("fx"), "t", grid))
                ys_all += list(evaluate(series.get("fy"), "t", grid))
            elif skind == "vline":
                vals = series.get("x")
                xs_all += _floats(vals if isinstance(vals, list) else [vals], "x")
            elif skind == "hline":
                vals = series.get("y")
                ys_all += _floats(vals if isinstance(vals, list) else [vals], "y")
            elif skind in ("hist", "stem", "barh", "stacked_bar", "box", "pie", "graph"):
                # 追加的通用数据图元：Pillow 没有自动缩放，值域由共享几何函数给出
                extra_x, extra_y = series_extent(series)
                xs_all += extra_x
                ys_all += extra_y
        if not xs_all:
            xs_all = [0.0, 1.0]
        if not ys_all:
            ys_all = [0.0, 1.0]
        xlim = _floats(panel["xlim"], "xlim") if panel.get("xlim") else [min(xs_all), max(xs_all)]
        ylim = _floats(panel["ylim"], "ylim") if panel.get("ylim") else [min(ys_all), max(ys_all)]
        if xlim[0] == xlim[1]:
            xlim = [xlim[0] - 1.0, xlim[1] + 1.0]
        if ylim[0] == ylim[1]:
            ylim = [ylim[0] - 1.0, ylim[1] + 1.0]
        pad = (ylim[1] - ylim[0]) * 0.08
        ylim = [ylim[0] - pad, ylim[1] + pad]

        if panel.get("aspect") == "equal":
            # 真正等比例：让 x/y 共用同一个 px-per-unit。
            # 不做这一步，莫尔圆会被画框长宽比压成椭圆——这是"看起来对、其实错"的典型。
            frame_w = float(r_ - l)
            frame_h = float(b - t)
            x_span = float(xlim[1] - xlim[0])
            y_span = float(ylim[1] - ylim[0])
            if frame_w > 0 and frame_h > 0 and x_span > 0 and y_span > 0:
                current = frame_w / frame_h
                target = x_span / y_span
                if target < current:
                    extra = (y_span * current - x_span) / 2.0
                    xlim = [xlim[0] - extra, xlim[1] + extra]
                elif target > current:
                    extra = (x_span / current - y_span) / 2.0
                    ylim = [ylim[0] - extra, ylim[1] + extra]

        # 整齐刻度：等比例面板（莫尔圆等）**不能**吸附范围，否则圆会被压成椭圆，
        # 所以只挑范围内的整齐刻度；其余面板连范围一起吸附，顺便避免全正数据跨到负值区。
        xt = _nice_ticks(xlim[0], xlim[1], 5)
        yt = _nice_ticks(ylim[0], ylim[1], 5)
        if panel.get("aspect") != "equal":
            if len(xt[2]) >= 2:
                xlim = [xt[0], xt[1]]
            if len(yt[2]) >= 2:
                ylim = [yt[0], yt[1]]
        eps = 1e-9
        x_ticks = [v for v in xt[2] if xlim[0] - eps <= v <= xlim[1] + eps] or \
                  [xlim[0] + (xlim[1] - xlim[0]) * i / 4.0 for i in range(5)]
        y_ticks = [v for v in yt[2] if ylim[0] - eps <= v <= ylim[1] + eps] or \
                  [ylim[0] + (ylim[1] - ylim[0]) * i / 4.0 for i in range(5)]

        def px(x):
            return l + (float(x) - xlim[0]) / (xlim[1] - xlim[0]) * (r_ - l)

        def py(y):
            return b - (float(y) - ylim[0]) / (ylim[1] - ylim[0]) * (b - t)

        draw.rectangle([l, t, r_, b], outline="#cfd8dc")
        for value in x_ticks:
            gx = px(value)
            draw.line([gx, t, gx, b], fill="#eceff1")
            if not panel.get("hide_xticklabels"):
                draw.text((gx, b + int(6 * scale)), "%.3g" % value,
                          font=f_tick, fill="#546e7a", anchor="ma")
        for value in y_ticks:
            gy = py(value)
            draw.line([l, gy, r_, gy], fill="#eceff1")
            draw.text((l - int(8 * scale), gy), "%.3g" % value,
                      font=f_tick, fill="#546e7a", anchor="rm")
        draw.rectangle([l, t, r_, b], outline="#90a4ae")
        if panel.get("xlabel"):
            draw.text(((l + r_) / 2.0, b + int(24 * scale)), str(panel["xlabel"]), font=f_axis, fill="#263238", anchor="ma")
        if panel.get("ylabel"):
            draw.text((l - int(52 * scale), (t + b) / 2.0), str(panel["ylabel"]), font=f_axis, fill="#263238", anchor="mm")
        if panel.get("title"):
            draw.text((l, top + int(4 * scale)), str(panel["title"]), font=f_axis, fill="#1a1a1a", anchor="la")

        for order, series in enumerate(series_list):
            skind = series.get("kind")
            if skind not in PILLOW_KINDS:
                continue
            color = series.get("color") or DEFAULT_COLORS[order % len(DEFAULT_COLORS)]
            width_px = max(1, int(float(series.get("width", 2.0)) * scale))
            kinds.append(skind)
            if skind in ("line", "step", "scatter", "area", "bar"):
                yv = _floats(series.get("y"), "y")
                xv = _floats(series["x"], "x") if series.get("x") else list(range(len(yv)))
                pts = [(px(x), py(y)) for x, y in zip(xv, yv)]
                if skind == "bar":
                    half = (r_ - l) / max(len(pts) * 2.4, 1)
                    for (cx, cy), y in zip(pts, yv):
                        draw.rectangle([cx - half, cy, cx + half, py(0.0)], fill=color)
                elif skind == "scatter":
                    for cx, cy in pts:
                        draw.ellipse([cx - 4 * scale, cy - 4 * scale, cx + 4 * scale, cy + 4 * scale], fill=color)
                elif skind == "area":
                    draw.polygon(pts + [(pts[-1][0], py(0.0)), (pts[0][0], py(0.0))],
                                 fill=color + "55" if len(color) == 7 else color)
                elif skind == "step":
                    path = []
                    for i, (cx, cy) in enumerate(pts):
                        path.append((cx, cy if i == 0 else path[-1][1]))
                    for (cx, cy), (_, cy2) in zip(pts, pts[1:]):
                        path.append((cx, cy2))
                    if len(path) > 1:
                        draw.line(path, fill=color, width=width_px, joint="curve")
                elif len(pts) > 1:
                    draw.line(pts, fill=color, width=width_px, joint="curve")
            elif skind == "function":
                rng = series.get("xrange") or [xlim[0], xlim[1]]
                grid = np.linspace(float(rng[0]), float(rng[1]), int(series.get("n") or 300))
                yv = evaluate(series.get("expr"), "x", grid)
                pts = [(px(x), py(y)) for x, y in zip(grid, yv) if math.isfinite(float(y))]
                if len(pts) > 1:
                    draw.line(pts, fill=color, width=width_px, joint="curve")
            elif skind == "parametric":
                rng = series.get("trange") or [0, 2 * math.pi]
                grid = np.linspace(float(rng[0]), float(rng[1]), int(series.get("n") or 300))
                pts = [(px(x), py(y)) for x, y in zip(evaluate(series.get("fx"), "t", grid),
                                                      evaluate(series.get("fy"), "t", grid))]
                if len(pts) > 1:
                    draw.line(pts, fill=color, width=width_px, joint="curve")
            elif skind == "polygon":
                pts = [(px(p[0]), py(p[1])) for p in series.get("xy") or []]
                if len(pts) > 2:
                    draw.polygon(pts, outline=color, fill=color + "33" if len(color) == 7 else None)
            elif skind == "circle":
                center = _floats(series.get("center"), "center")
                radius = float(series.get("r"))
                cx, cy = px(center[0]), py(center[1])
                rx = abs(px(center[0] + radius) - cx)
                ry = abs(py(center[1] + radius) - cy)
                draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], outline=color, width=width_px)
            elif skind == "ellipse":
                center = _floats(series.get("center"), "center")
                w = float(series.get("width"))
                h = float(series.get("height"))
                cx, cy = px(center[0]), py(center[1])
                rx = abs(px(center[0] + w / 2.0) - cx)
                ry = abs(py(center[1] + h / 2.0) - cy)
                draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], outline=color, width=width_px)
            elif skind in ("vline", "hline"):
                key = "x" if skind == "vline" else "y"
                vals = series.get(key)
                seq = vals if isinstance(vals, list) else [vals]
                for value in _floats(seq, key):
                    if skind == "vline":
                        draw.line([px(value), t, px(value), b], fill=color, width=width_px)
                    else:
                        draw.line([l, py(value), r_, py(value)], fill=color, width=width_px)
            elif skind in ("text", "annotate"):
                pos = _floats(series.get("xy"), "xy")
                cx, cy = px(pos[0]), py(pos[1])
                txt = str(series.get("text", ""))
                if skind == "annotate" and series.get("arrow", True):
                    offset = _floats(series.get("xytext") or [40, -30], "xytext")
                    tx = cx + offset[0] * scale
                    ty = cy + offset[1] * scale
                    draw.line([cx, cy, tx, ty], fill=color, width=max(1, int(scale)))
                    _pillow_label(draw, (tx, ty), txt, f_val, color, scale, "lm")
                else:
                    # 尊重调用方给的 ha/va（预设靠它把标注推到图形外侧），并加白底保证压图可读
                    horiz = {"left": "l", "center": "m", "right": "r"}.get(
                        str(series.get("ha") or "center"), "m")
                    vert = {"bottom": "b", "center": "m", "top": "t"}.get(
                        str(series.get("va") or "center"), "m")
                    _pillow_label(draw, (cx, cy), txt, f_val, color, scale, horiz + vert)
            elif skind == "arrow":
                xy = _floats(series.get("xy"), "xy")
                tail = _floats(series.get("xytext"), "xytext")
                draw.line([px(tail[0]), py(tail[1]), px(xy[0]), px(xy[1])], fill=color, width=width_px)
            # ---- 通用数据图元（学科无关的追加层；Pillow 实现，与 matplotlib 同几何）----
            elif skind == "hist":
                counts, edges = histogram_counts(series)
                mark = _rgba(color, 0.85)
                for index, count in enumerate(counts):
                    x0, x1 = px(edges[index]), px(edges[index + 1])
                    if x1 < x0:
                        x0, x1 = x1, x0
                    inset = (x1 - x0) * 0.03
                    draw.rectangle([x0 + inset, py(count), x1 - inset, py(0.0)], fill=mark,
                                   outline=_rgba(series.get("edgecolor", "#ffffff")),
                                   width=max(1, int(scale)))
            elif skind == "stem":
                obs = series_observations(series, ("values", "y"))
                raw_x = series.get("x")
                if isinstance(raw_x, list) and raw_x and len(raw_x) == len(obs):
                    xv = _floats(raw_x, "x")
                else:
                    xv = [float(i) for i in range(len(obs))]
                base = py(float(series.get("baseline", 0.0)))
                dot = max(2.0, float(series.get("size", 5.0)) * scale * 0.9)
                for x, y in zip(xv, obs):
                    draw.line([px(x), base, px(x), py(y)], fill=color, width=width_px)
                    draw.ellipse([px(x) - dot, py(y) - dot, px(x) + dot, py(y) + dot], fill=color)
            elif skind == "barh":
                widths = _floats(_require(series, "x", list, "barh"), "x")
                raw_y = series.get("y")
                if isinstance(raw_y, list) and raw_y:
                    yv = _floats(raw_y, "y")
                    if len(yv) != len(widths):
                        raise SpecError("barh 的 x 与 y 长度不一致（%d vs %d）" % (len(widths), len(yv)))
                else:
                    yv = [float(i) for i in range(len(widths))]
                step = min([abs(yv[i + 1] - yv[i]) for i in range(len(yv) - 1)] or [1.0])
                half = abs(py(0.0) - py(step)) * float(series.get("bar_height", 0.7)) / 2.0
                zero = px(0.0)
                for y, width_value in zip(yv, widths):
                    draw.rectangle([min(zero, px(width_value)), py(y) - half,
                                    max(zero, px(width_value)), py(y) + half],
                                   fill=_rgba(color, 0.9))
            elif skind == "stacked_bar":
                xv, rows, _labels = stacked_rows(series)
                palette = stack_colors(series, len(rows))
                step = min([abs(xv[i + 1] - xv[i]) for i in range(len(xv) - 1)] or [1.0])
                half = abs(px(0.0) - px(step)) * float(series.get("bar_width", 0.8)) / 2.0
                positive = [0.0] * len(xv)
                negative = [0.0] * len(xv)
                for index, row in enumerate(rows):
                    mark = _rgba(palette[index], 0.92)
                    for position, value in enumerate(row):
                        cx = px(xv[position])
                        if value >= 0:
                            draw.rectangle([cx - half, py(positive[position] + value), cx + half,
                                            py(positive[position])], fill=mark)
                            positive[position] += value
                        else:
                            draw.rectangle([cx - half, py(negative[position]), cx + half,
                                            py(negative[position] + value)], fill=mark)
                            negative[position] += value
            elif skind == "box":
                stats = box_stats_list(series)
                width = float(series.get("box_width", 0.55))
                palette = stack_colors(series, len(stats))
                for index, five in enumerate(stats):
                    low, q1, median, q3, high = five
                    mark = _rgba(palette[index])
                    cx = px(float(index + 1))
                    x0 = px(float(index + 1) - width / 2.0)
                    x1 = px(float(index + 1) + width / 2.0)
                    draw.rectangle([x0, py(q3), x1, py(q1)], fill=_rgba(palette[index], 0.22),
                                   outline=mark, width=max(1, width_px - 1))
                    draw.line([x0, py(median), x1, py(median)], fill=mark, width=max(2, width_px))
                    draw.line([cx, py(q3), cx, py(high)], fill=mark, width=width_px)
                    draw.line([cx, py(low), cx, py(q1)], fill=mark, width=width_px)
                    cap = (x1 - x0) * 0.28
                    draw.line([cx - cap, py(high), cx + cap, py(high)], fill=mark, width=width_px)
                    draw.line([cx - cap, py(low), cx + cap, py(low)], fill=mark, width=width_px)
                labels = series.get("labels") or []
                if labels:
                    lows = [five[0] for five in stats]
                    highs = [five[4] for five in stats]
                    span = max(max(highs) - min(lows), 1e-9)
                    label_y = float(min(lows)) - span * 0.025
                    for index in range(min(len(labels), len(stats))):
                        _pillow_label(draw, (px(float(index + 1)), py(label_y)), str(labels[index]),
                                      f_tick, "#546e7a", scale, "mm")
            elif skind == "pie":
                values = _floats(_require(series, "values", list, "pie"), "values")
                labels = series.get("labels") or []
                slices = pie_slices(values, float(series.get("startangle", 90.0)))
                palette = stack_colors(series, len(slices))
                alpha_value = float(series.get("alpha", 1.0))
                cx0, cy0 = px(0.0), py(0.0)
                # 真实半径取 x/y 两个方向的最小值：非等比例画框里也是正圆，不会被压成椭圆
                radius = min(abs(px(1.0) - cx0), abs(py(1.0) - cy0)) * float(series.get("radius", 0.78))
                for index, wedge in enumerate(slices):
                    draw.pieslice([cx0 - radius, cy0 - radius, cx0 + radius, cy0 + radius],
                                  -wedge["hi"], -wedge["lo"], fill=_rgba(palette[index], alpha_value),
                                  outline=(255, 255, 255, 255), width=max(1, int(scale)))
                    mid = math.radians((wedge["lo"] + wedge["hi"]) / 2.0)
                    text = str(labels[index]) if index < len(labels) else ""
                    if series.get("show_percent", True):
                        text = ("%s %.0f%%" % (text, wedge["frac"] * 100.0)).strip()
                    horiz = "l" if math.cos(mid) > 0.18 else ("r" if math.cos(mid) < -0.18 else "m")
                    vert = "b" if math.sin(mid) > 0.18 else ("t" if math.sin(mid) < -0.18 else "m")
                    _pillow_label(draw, (cx0 + math.cos(mid) * radius * 1.28,
                                         cy0 - math.sin(mid) * radius * 1.28),
                                  text, f_tick, "#333333", scale, horiz + vert)
            elif skind == "table":
                columns = [str(c) for c in (series.get("columns") or [])]
                rows = [[("" if v is None else str(v)) for v in row]
                        for row in (series.get("rows") or [])]
                if not columns and not rows:
                    raise SpecError("table 需要 columns[] 或 rows[[..],..]")
                # 表格没有坐标系：整行清掉刻度标签与轴名（等价于 matplotlib 的 axis("off")），
                # 再把子图标题补回原位，避免出现"表格旁边还有一套无意义刻度"
                draw.rectangle([0, top, W, top + each], fill="white")
                if panel.get("title"):
                    draw.text((l, top + int(4 * scale)), str(panel["title"]), font=f_axis,
                              fill="#1a1a1a", anchor="la")
                body = ([columns] if columns else []) + rows
                ncol = max([len(columns)] + [len(row) for row in rows] + [1])
                nrow = max(len(body), 1)
                cell_w = (r_ - l) / float(ncol)
                cell_h = (b - t) / float(nrow)
                line_color = (207, 216, 220, 255)
                for index in range(nrow + 1):
                    draw.line([l, t + cell_h * index, r_, t + cell_h * index], fill=line_color,
                              width=max(1, int(scale)))
                for index in range(ncol + 1):
                    draw.line([l + cell_w * index, t, l + cell_w * index, b], fill=line_color,
                              width=max(1, int(scale)))
                for row_index, row in enumerate(body):
                    head = bool(columns) and row_index == 0
                    for col_index in range(ncol):
                        text = row[col_index] if col_index < len(row) else ""
                        _pillow_label(draw,
                                      (l + cell_w * (col_index + 0.5), t + cell_h * (row_index + 0.5)),
                                      text, f_axis if head else f_tick,
                                      "#22303c" if head else "#37474f", scale, "mm")
            elif skind == "graph":
                nodes, edges, _auto = graph_layout(series)
                node_face = series.get("node_color", "#e8f0fb")
                edge_color = series.get("edge_color", "#7b8794")
                label_size = float(series.get("size", 9.0))
                edge_lw = max(1, int(round(float(series.get("width", 1.6)) * scale)))
                for edge in edges:
                    p_from, p_to = trim_segment((edge["a"]["x"], edge["a"]["y"]),
                                                (edge["b"]["x"], edge["b"]["y"]),
                                                edge["a"]["r"], edge["b"]["r"])
                    draw.line([px(p_from[0]), py(p_from[1]), px(p_to[0]), py(p_to[1])],
                              fill=_rgba(edge_color), width=edge_lw)
                    if edge["directed"]:
                        head = arrow_head_points(p_from, p_to,
                                                 float(series.get("arrow_size") or 0.0)
                                                 or edge["b"]["r"] * 0.55)
                        if head:
                            draw.polygon([(px(q[0]), py(q[1])) for q in head], fill=_rgba(edge_color))
                    if edge.get("label"):
                        mx, my = (p_from[0] + p_to[0]) / 2.0, (p_from[1] + p_to[1]) / 2.0
                        dx, dy = p_to[0] - p_from[0], p_to[1] - p_from[1]
                        dist = math.hypot(dx, dy) or 1.0
                        offset = edge["a"]["r"] * 0.55
                        _pillow_label(draw, (px(mx - dy / dist * offset), py(my + dx / dist * offset)),
                                      str(edge["label"]), f_tick, "#54606e", scale, "mm")
                for node in nodes:
                    cx0, cy0 = px(node["x"]), py(node["y"])
                    radius = min(abs(px(node["x"] + node["r"]) - cx0),
                                 abs(py(node["y"] + node["r"]) - cy0))
                    radius = max(radius, 3.0)
                    draw.ellipse([cx0 - radius, cy0 - radius, cx0 + radius, cy0 + radius],
                                 fill=_rgba(node_face), outline=_rgba(color),
                                 width=max(1, width_px - 1))
                    _pillow_label(draw, (cx0, cy0), str(node["label"]), f_tick,
                                  "#22303c", scale, "mm")

    if spec.get("note"):
        for i, note in enumerate(spec["note"][:3]):
            draw.text((int(20 * scale), int(height * scale) - int((48 - i * 16) * scale)),
                      "· " + str(note), font=f_tick, fill="#607d8b", anchor="la")
    img.save(out)
    return {"backend": "pillow", "unsupported": sorted(set(unsupported)), "kinds": sorted(set(kinds)),
            "panels": len(panels)}


# --------------------------------------------------------------------------
# 预设展开：把「学科数据」翻译成通用 axes（唯一允许出现学科概念的地方就是 presets.py）
# --------------------------------------------------------------------------
def normalize_spec(spec):
    """展开 spec / panels 里的 `preset`，返回 (新 spec, 用到的预设名, 提示)。"""
    if not isinstance(spec, dict):
        raise SpecError("spec 必须是 JSON 对象")
    top_preset = bool(spec.get("preset"))
    panels = spec.get("panels")
    panel_presets = isinstance(panels, list) and any(
        isinstance(item, dict) and item.get("preset") for item in panels
    )
    if not top_preset and not panel_presets:
        return dict(spec), [], []

    if preset_registry is None:
        raise SpecError("预设模块 presets.py 不可用（%s）" % (PRESET_IMPORT_ERROR or "未知原因"))

    used = []
    notes = []
    axes = []
    built_title = None
    try:
        if top_preset:
            built = preset_registry.expand(spec)
            used.append(built["preset"])
            notes += built.get("note") or []
            built_title = built.get("title")
            axes = list(built["axes"])
        else:
            multi = len(panels) > 1
            for index, panel in enumerate(panels):
                if not isinstance(panel, dict):
                    raise SpecError("panels[%d] 必须是对象" % index)
                if panel.get("preset"):
                    # 多图纵向排列时，标注改走"朝零线一侧"，否则会撞上一个子图的标题
                    source = dict(panel)
                    if multi:
                        source["annotate_inside"] = True
                    built = preset_registry.expand(source)
                    used.append(built["preset"])
                    notes += built.get("note") or []
                    # panel 级 main_title 原先也被静默丢掉：expand 把它放在 built["title"] 里，
                    # 而这里只 extend 了 axes，标题就没了。显式给的名字要覆盖预设默认轴标题。
                    if built.get("title"):
                        for axis in built["axes"]:
                            if isinstance(axis, dict):
                                axis["title"] = built["title"]
                    axes.extend(built["axes"])
                else:
                    axes.append(panel)
    except SpecError:
        raise
    except Exception as exc:
        raise SpecError("预设展开失败：%s" % exc)

    consumed = set(preset_registry.consumed_keys())
    merged = {key: value for key, value in spec.items() if key not in consumed}
    merged.pop("panels", None)
    merged["axes"] = axes

    # 单张预设图：轴标题与图标题是同一句话，只能留一处。原先只在"图标题为空"时才上提，
    # 于是显式传 title 时同一句被画两遍（大标题 + 轴标题，轴标题还压住右上信息行）。
    if top_preset and len(axes) == 1 and axes[0].get("title"):
        # 标题优先级：built_title（预设层给的，含调用方显式写的 main_title/预设默认名）
        #             > 轴上的标题。原先先取轴标题，会把 main_title 顶掉。
        if not merged.get("title"):
            merged["title"] = built_title or axes[0]["title"]
        axes[0]["title"] = None
    if built_title and not merged.get("title"):
        merged["title"] = built_title
    # 顶层 main_title + panels 组合：原先只有"单张顶层预设"才认 main_title，
    # 于是带 panels 时它会**静默消失**（画面上什么都没有，也不报错）。
    # 这里把它提升为整幅图的标题，与单张预设的语义一致。
    if spec.get("main_title") and not merged.get("title"):
        merged["title"] = spec["main_title"]
    merged.pop("main_title", None)
    if notes:
        merged["note"] = list(merged.get("note") or []) + notes

    # 多张阶梯图（例如 Q/M 共享 x 轴）：统一 xlim，且只让最下面一张显示 x 刻度标签
    sharex = True
    if isinstance(spec.get("grid"), dict) and spec["grid"].get("sharex") is False:
        sharex = False
    if len(axes) > 1 and sharex:
        limits = [item.get("xlim") for item in axes if isinstance(item, dict) and item.get("xlim")]
        if limits:
            low = min(float(limit[0]) for limit in limits)
            high = max(float(limit[1]) for limit in limits)
            for item in axes:
                if isinstance(item, dict):
                    item["xlim"] = [low, high]
        for index, item in enumerate(axes):
            if isinstance(item, dict) and index < len(axes) - 1:
                item.setdefault("hide_xticklabels", True)
                # 共享 x 轴时上面那些子图的轴名是多余的（会和下一个子图的标题挤在一起）
                item["xlabel"] = None
        merged.setdefault("grid", {"rows": len(axes), "cols": 1, "sharex": True})

    return merged, used, []


# --------------------------------------------------------------------------
def render(spec):
    if not isinstance(spec, dict):
        raise SpecError("spec 必须是 JSON 对象")
    spec, presets_used, preset_warnings = normalize_spec(spec)
    out = _require(spec, "out", str, "spec")
    width = int(spec.get("width") or 1400)
    height = int(spec.get("height") or 900)
    scale = float(spec.get("dpi_scale") or 2.0)
    if width < 120 or height < 120:
        raise SpecError("width/height 至少 120")
    axes = spec.get("axes") or [{"slot": [0, 0]}]
    if not isinstance(axes, list) or not axes:
        raise SpecError("axes 必须是非空数组")
    for panel in axes:
        if not isinstance(panel, dict):
            raise SpecError("axes 的每一项必须是对象")
        if panel.get("type") and panel["type"] not in AXES_TYPES + ["surface"]:
            raise SpecError("未知坐标系 %r" % panel["type"])

    requested = spec.get("backend") or "auto"
    if requested not in ("auto", "matplotlib", "pillow"):
        raise SpecError("backend 只能是 auto/matplotlib/pillow")
    warnings = []
    has_matplotlib = True
    if requested != "pillow":
        try:
            import matplotlib  # noqa: F401
        except Exception:
            has_matplotlib = False
            if requested == "matplotlib":
                raise SpecError("指定了 matplotlib 后端，但该解释器没有 matplotlib：%s"
                                % sys.executable)
            warnings.append("该解释器没有 matplotlib，已退回 Pillow 后端（等高线/3D/极坐标/误差棒不可用）")

    out_dir = os.path.dirname(os.path.abspath(out))
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    if has_matplotlib and requested != "pillow":
        info = render_matplotlib(spec, out, width, height, scale)
    else:
        info = render_pillow(spec, out, width, height, scale)

    if not os.path.exists(out):
        raise SpecError("渲染未生成文件：%s" % out)
    from PIL import Image
    with Image.open(out) as image:
        final_w, final_h = image.size
    summary = {
        "ok": True, "out": out, "backend": info["backend"], "width": final_w, "height": final_h,
        "bytes": os.path.getsize(out), "panels": info["panels"], "kinds": info["kinds"],
        "series_count": sum(len(p.get("series") or []) for p in axes),
    }
    if presets_used:
        summary["presets"] = presets_used
    if info.get("unsupported"):
        summary["unsupported"] = info["unsupported"]
    warnings = list(warnings) + list(preset_warnings)
    if warnings:
        summary["warnings"] = warnings
    # 画布异常放大通常意味着有元素被画到坐标范围外（例如标注偏移用错单位）
    if final_w > width * scale * 2.5 or final_h > height * scale * 2.5:
        summary.setdefault("warnings", []).append(
            "画布尺寸异常放大（%dx%d，请求 %dx%d）：通常是有元素落在坐标范围外，请检查标注偏移或数据范围"
            % (final_w, final_h, int(width * scale), int(height * scale))
        )
    return summary


def main(argv):
    spec = None
    i = 0
    while i < len(argv):
        if argv[i] == "--spec" and i + 1 < len(argv):
            with open(argv[i + 1], "r", encoding="utf-8") as handle:
                spec = json.load(handle)
            i += 2
        elif argv[i] == "--spec-json" and i + 1 < len(argv):
            spec = json.loads(argv[i + 1])
            i += 2
        else:
            i += 1
    if spec is None:
        sys.stdout.write(json.dumps({"ok": False, "error": "缺少 --spec 或 --spec-json"}, ensure_ascii=False))
        return 1
    try:
        result = render(spec)
    except SpecError as exc:
        sys.stdout.write(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    except Exception as exc:  # 渲染期异常也要给出可读原因
        sys.stdout.write(json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)},
                                    ensure_ascii=False))
        return 1
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))