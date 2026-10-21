#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""figure.py 的「学科预设 + 通用数据图元」覆盖矩阵自测（离线，子进程调 figure.py）。

用法：
    <python> figure_cases.py [--python <解释器>] [--out <目录>] [--only <名称前缀>]
                             [--expect-backend matplotlib|pillow]

覆盖：
  * 7 个学科预设（axial / shear / moment / torque / stress / mohr / deflection）各一例，
    外加 panels 共享 x 轴的 Q/M 双图 —— 属于回归，确保新图元没有弄坏预设层；
  * 8 个通用数据图元各一例：hist / stem / barh / stacked_bar / pie / box / table / graph；
  * aspect=equal 的**像素级**等比例断言（量测有色像素外接框，两个后端都必须是正圆）；
  * hide_xticklabels、Pillow 后端 unsupported 是否如实上报（不静默画错）；
  * 错误路径（未知 preset / 缺字段 / 空数据 / 未知系列）必须给出可读中文 JSON 错误。

每个用例断言：ok==true、退出码 0、文件落盘且字节数 > 0、unsupported 与预期一致、
没有「画布异常放大」告警。stdout 是人读摘要 + 恰好一个 JSON 汇总；末尾列出所有产物 PNG 绝对路径。
"""

import json
import os
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
FIGURE = os.path.join(SCRIPTS, "figure.py")

# 用例里以这些键开头的字段是"期望"，不写进 spec
CASE_KEYS = ("expect_error", "error_contains", "expect_presets", "expect_kinds",
             "expect_unsupported", "expect_supported", "expect_no_unsupported",
             "check_circle", "measure_color")

NEW_KINDS = ["hist", "stem", "barh", "stacked_bar", "pie", "box", "table", "graph"]


def cases(backend):
    """返回 {用例名: spec + 期望}。backend 影响 unsupported 的期望值。"""
    pillow = backend == "pillow"
    specs = {}

    # ---------------- 预设回归（7 个预设 + panels 共享 x 轴） ----------------
    specs["preset_axial"] = {
        "preset": "axial", "unit": "kN", "x_label": "x (m)",
        "segments": [{"x0": 0, "x1": 2, "value": 20, "label": "AB"},
                     {"x0": 2, "x1": 4, "value": -30, "label": "BC"},
                     {"x0": 4, "x1": 6, "value": -30, "label": "CD"}],
        "main_title": "轴力图 N（预设回归）",
        "expect_presets": ["axial"], "expect_no_unsupported": True,
    }
    specs["preset_shear"] = {
        "preset": "shear", "unit": "kN", "x_label": "x (m)",
        "segments": [{"x0": 0, "x1": 2, "value": 5}, {"x0": 2, "x1": 4, "value": -5}],
        "main_title": "剪力图 Q（预设回归）",
        "expect_presets": ["shear"], "expect_no_unsupported": True,
    }
    specs["preset_torque"] = {
        "preset": "torque", "unit": "N·m", "x_label": "x (m)",
        "segments": [{"x0": 0, "x1": 1, "value": 12}, {"x0": 1, "x1": 3, "value": -8},
                     {"x0": 3, "x1": 4, "value": 0}],
        "main_title": "扭矩图 T（预设回归）",
        "expect_presets": ["torque"], "expect_no_unsupported": True,
    }
    # moment：segments 走阶梯图，points 走曲线 + 极值标注带（缺陷 2 的回归）
    specs["preset_moment_segments"] = {
        "preset": "moment", "unit": "kN·m", "x_label": "x (m)",
        "segments": [{"x0": 0, "x1": 2, "value": 10}, {"x0": 2, "x1": 4, "value": 0}],
        "main_title": "弯矩图 M（阶梯型，预设回归）",
        "expect_presets": ["moment"], "expect_no_unsupported": True,
    }
    specs["preset_moment_points"] = {
        "preset": "moment", "unit": "kN·m", "x_label": "x (m)", "show_extremum": True,
        "points": [{"x": 0, "value": 0}, {"x": 1, "value": 3.75}, {"x": 2, "value": 10},
                   {"x": 3, "value": 3.75}, {"x": 4, "value": 0}],
        "main_title": "弯矩图 M（跨中极值标注回归）",
        "expect_presets": ["moment"], "expect_no_unsupported": True,
    }
    specs["preset_deflection"] = {
        "preset": "deflection", "unit": "mm", "x_label": "x (m)", "show_extremum": True,
        "points": [{"x": 0, "value": 0}, {"x": 1, "value": -2.1}, {"x": 2, "value": -3.0},
                   {"x": 3, "value": -2.1}, {"x": 4, "value": 0}],
        "main_title": "挠曲线 v（预设回归）",
        "expect_presets": ["deflection"], "expect_no_unsupported": True,
    }
    specs["preset_mohr"] = {
        "preset": "mohr", "sigma_x": 80, "sigma_y": -50, "tau_xy": 40, "unit": "MPa",
        "main_title": "莫尔应力圆（等比例 + 标注带回归）",
        "expect_presets": ["mohr"], "expect_no_unsupported": True,
        # 只量测、不作为通过条件：圆弧是 #1c60be，外接框宽高比应接近 1（真正等比例）
        "measure_color": "#1c60be",
    }
    specs["preset_stress_linear"] = {
        "preset": "stress", "unit": "MPa", "type": "linear",
        "dist": [{"y": -0.15, "sigma": -40}, {"y": -0.075, "sigma": -20}, {"y": 0, "sigma": 0},
                 {"y": 0.075, "sigma": 20}, {"y": 0.15, "sigma": 40}],
        "main_title": "应力分布 σ（线性，轴名分行回归）",
        "expect_presets": ["stress"], "expect_no_unsupported": True,
    }
    specs["preset_stress_parabolic"] = {
        "preset": "stress", "unit": "MPa", "type": "parabolic",
        "dist": [{"y": -0.15, "sigma": 0}, {"y": 0, "sigma": 45}, {"y": 0.15, "sigma": 0}],
        "main_title": "应力分布 σ（抛物线型）",
        "expect_presets": ["stress"], "expect_no_unsupported": True,
    }
    specs["preset_panels_qm"] = {
        "panels": [
            {"preset": "shear", "unit": "kN",
             "segments": [{"x0": 0, "x1": 2, "value": 5}, {"x0": 2, "x1": 4, "value": -5}]},
            {"preset": "moment", "unit": "kN·m", "show_extremum": True,
             "points": [{"x": 0, "value": 0}, {"x": 2, "value": 10}, {"x": 4, "value": 0}]},
        ],
        "title": "剪力图与弯矩图（共享 x 轴）", "x_label": "x (m)", "height": 760,
        "expect_presets": ["shear", "moment"], "expect_no_unsupported": True,
    }

    # ---------------- 新增通用数据图元（学科无关） ----------------
    specs["kind_hist"] = {
        "title": "直方图（通用图元 hist）", "height": 640,
        "axes": [{"xlabel": "区间", "ylabel": "频数", "series": [
            {"kind": "hist", "label": "实测样本", "bins": 6, "color": "#2f6fbf",
             "values": [12.1, 12.4, 13.0, 13.2, 13.3, 13.8, 14.0, 14.1, 14.2, 14.5,
                        14.6, 14.9, 15.0, 15.2, 15.5, 15.8, 16.0, 16.4, 17.1, 13.6]},
        ]}],
        "expect_kinds": ["hist"], "expect_no_unsupported": True,
    }
    specs["kind_stem"] = {
        "title": "火柴杆图（通用图元 stem）", "height": 640,
        "axes": [{"xlabel": "序号", "ylabel": "幅值", "series": [
            {"kind": "stem", "label": "采样序列", "color": "#c0392b",
             "x": [1, 2, 3, 4, 5, 6, 7, 8], "y": [3, 1, 4, 1, 5, 9, 2, 6], "baseline": 0},
        ]}],
        "expect_kinds": ["stem"], "expect_no_unsupported": True,
    }
    specs["kind_barh"] = {
        "title": "条形图（通用图元 barh）", "height": 640,
        "axes": [{"xlabel": "长度 (mm)", "ylabel": "试件", "series": [
            {"kind": "barh", "label": "伸长量", "color": "#3f9a5a",
             "x": [12.0, 30.5, 18.2, 24.0], "y": [0, 1, 2, 3]},
        ]}],
        "expect_kinds": ["barh"], "expect_no_unsupported": True,
    }
    specs["kind_stacked_bar"] = {
        "title": "堆叠柱状图（通用图元 stacked_bar）", "height": 640,
        "axes": [{"xlabel": "工况", "ylabel": "荷载 (kN)", "legend": True, "series": [
            {"kind": "stacked_bar", "x": [1, 2, 3, 4], "ys": [[3, 4, 2, 5], [2, 1, 3, 2], [1, 2, 2, 1]],
             "labels": ["恒载", "活载", "其他"]},
        ]}],
        "expect_kinds": ["stacked_bar"], "expect_no_unsupported": True,
    }
    specs["kind_pie"] = {
        "title": "饼图（通用图元 pie）", "width": 900, "height": 760,
        "axes": [{"aspect": "equal", "series": [
            {"kind": "pie", "values": [35, 25, 20, 12, 8],
             "labels": ["材料", "人工", "机械", "管理", "其他"], "startangle": 90},
        ]}],
        "expect_kinds": ["pie"], "expect_no_unsupported": True,
    }
    specs["kind_box"] = {
        "title": "箱线图（通用图元 box）", "height": 640,
        "axes": [{"ylabel": "强度 (MPa)", "series": [
            {"kind": "box", "labels": ["组 1", "组 2", "组 3"],
             "values": [[12, 13, 14, 15, 11, 13], [20, 22, 21, 19, 23, 25], [8, 9, 9, 10, 7, 12]]},
        ]}],
        "expect_kinds": ["box"], "expect_no_unsupported": True,
    }
    specs["kind_table"] = {
        "title": "表格（通用图元 table）", "height": 620,
        "axes": [{"series": [
            {"kind": "table",
             "columns": ["杆件", "面积 A (mm²)", "许用应力 (MPa)", "结论"],
             "rows": [["AB", "400", "160", "通过"], ["BC", "300", "120", "通过"], ["CD", "200", "100", "复算"]]},
        ]}],
        "expect_kinds": ["table"], "expect_no_unsupported": True,
    }
    specs["kind_graph"] = {
        "title": "节点-边图（显式坐标，通用图元 graph）", "width": 1000, "height": 620,
        "axes": [{"series": [
            {"kind": "graph", "size": 10,
             "nodes": [{"id": "in", "x": 0, "y": 0, "label": "输入"},
                       {"id": "calc", "x": 2, "y": 0, "label": "计算"},
                       {"id": "check", "x": 4, "y": 0, "label": "校核"},
                       {"id": "out", "x": 6, "y": 0, "label": "输出"}],
             "edges": [{"from": "in", "to": "calc", "directed": True},
                       {"from": "calc", "to": "check", "directed": True, "label": "M, Q"},
                       {"from": "check", "to": "out", "directed": True},
                       {"from": "check", "to": "calc", "directed": True, "label": "返修"}]},
        ]}],
        "expect_kinds": ["graph"], "expect_no_unsupported": True,
    }
    specs["kind_graph_auto"] = {
        "title": "节点-边图（自动环形布局 + 无向边）", "width": 820, "height": 760,
        "axes": [{"series": [
            {"kind": "graph", "size": 10, "edge_color": "#8d9aa8",
             "nodes": [{"id": "A", "label": "A"}, {"id": "B", "label": "B"}, {"id": "C", "label": "C"},
                       {"id": "D", "label": "D"}, {"id": "E", "label": "E"}],
             "edges": [{"from": "A", "to": "B", "label": "12"}, {"from": "B", "to": "C", "label": "8"},
                       {"from": "C", "to": "D", "label": "6"}, {"from": "D", "to": "E", "label": "9"},
                       {"from": "E", "to": "A", "label": "5"}, {"from": "A", "to": "C", "label": "3"}]},
        ]}],
        "expect_kinds": ["graph"], "expect_no_unsupported": True,
    }

    # ---------------- 引擎级行为 ----------------
    # aspect=equal：像素级断言"正圆"（Pillow 后端也必须真的等比例）
    specs["equal_aspect_circle"] = {
        "title": "等比例校验：红圆外接框必须是正方形", "width": 900, "height": 700,
        "axes": [{"aspect": "equal", "xlim": [0, 10], "ylim": [0, 6], "grid": False,
                  "xlabel": "x", "ylabel": "y", "series": [
                      {"kind": "circle", "center": [5, 3], "r": 2.0, "color": "#e00000", "width": 2.5},
                      {"kind": "line", "x": [0, 10], "y": [3, 3], "color": "#888888", "style": ":"},
                  ]}],
        "check_circle": "#e00000", "expect_kinds": ["circle"], "expect_no_unsupported": True,
    }
    specs["hide_xticklabels"] = {
        "title": "共享 x 轴：上子图隐藏 x 刻度标签", "height": 700,
        "axes": [
            {"hide_xticklabels": True, "title": "上", "ylabel": "Q (kN)", "series": [
                {"kind": "line", "x": [0, 1, 2, 3], "y": [1, 3, 2, 0]}]},
            {"title": "下", "xlabel": "x (m)", "ylabel": "M (kN·m)", "series": [
                {"kind": "line", "x": [0, 1, 2, 3], "y": [0, 2, 3, 0]}]},
        ],
        "expect_no_unsupported": True,
    }
    # Pillow 后端做不到的要如实进 unsupported（这里用既有图元 contour 验证上报机制没被弄坏）
    specs["pillow_unsupported_report"] = {
        "title": "unsupported 如实上报（contour 只有 matplotlib 支持）", "height": 620,
        "axes": [{"series": [
            {"kind": "line", "x": [0, 1, 2], "y": [0, 1, 0], "label": "基准线"},
            {"kind": "contour", "x": [0, 1, 2], "y": [0, 1, 2],
             "z": [[0, 1, 2], [1, 2, 3], [2, 3, 4]], "levels": 4},
        ]}],
        "expect_kinds": [] if pillow else ["contour"],
        "expect_unsupported": ["contour"] if pillow else [],
        "expect_supported": [] if pillow else ["contour"],
        "expect_no_unsupported": not pillow,
    }

    # ---------------- 表达式系列（function / parametric）----------------
    # 真实踩到过：表达式求值只认 Python 的 `**`，于是 `cos(x)^3` 被当成**按位异或**，
    # 报出 `ufunc 'bitwise_xor' not supported ...` 这种谁也读不懂的 numpy 原生错误；
    # 而同包的 symbolic.py 是接受 `^` 的——一套工具两种约定，模型必然写错。
    # 这条用例同时钉住 `^`（归一为乘方）与 `**`（原样），并要求两条曲线数值一致。
    specs["expr_caret_power"] = {
        "axes": [{"xlabel": "x", "legend": True, "series": [
            {"kind": "function", "expr": "3/(1+4*cos(x*pi/180)^3)", "xrange": [0, 90],
             "label": "N1/F（^ 写法）", "color": "#1f77b4"},
            {"kind": "function", "expr": "3/(1+4*cos(x*pi/180)**3)", "xrange": [0, 90],
             "label": "N1/F（** 写法）", "color": "#d62728", "linestyle": "--"},
            {"kind": "function", "expr": "6*cos(x*pi/180)^2/(1+4*cos(x*pi/180)^3)", "xrange": [0, 90],
             "label": "N2/F（题1-32）", "color": "#2ca02c"},
        ]}],
        "title": "函数系列：^ 与 ** 都表示乘方（题 1-32 内力随 α 变化）",
        "expect_kinds": ["function"], "expect_no_unsupported": True,
    }
    specs["expr_parametric_caret"] = {
        "axes": [{"xlabel": "x", "ylabel": "y", "series": [
            {"kind": "parametric", "fx": "cos(t)", "fy": "sin(t)", "trange": [0, 6.2832],
             "label": "单位圆"},
            {"kind": "parametric", "fx": "cos(t)^3", "fy": "sin(t)^3", "trange": [0, 6.2832],
             "label": "星形线（^ 写法）"},
        ]}],
        "title": "参数曲线：^ 乘方回归",
        "expect_kinds": ["parametric"], "expect_no_unsupported": True,
    }

    # ---------------- 错误路径（必须是可读中文 JSON 错误） ----------------
    specs["bad_preset_unknown"] = {
        "preset": "bending_moment", "segments": [{"x0": 0, "x1": 1, "value": 1}],
        "expect_error": True, "error_contains": "preset",
    }
    specs["bad_preset_missing_field"] = {
        "preset": "moment", "unit": "kN·m",
        "expect_error": True, "error_contains": "points",
    }
    specs["bad_preset_empty_segments"] = {
        "preset": "shear", "segments": [],
        "expect_error": True, "error_contains": "segments",
    }
    specs["bad_kind_empty_hist"] = {
        "axes": [{"series": [{"kind": "hist", "values": []}]}],
        "expect_error": True, "error_contains": "非空",
    }
    specs["bad_kind_unknown"] = {
        "axes": [{"series": [{"kind": "spiral", "y": [1, 2]}]}],
        "expect_error": True, "error_contains": "未知系列",
    }
    # 表达式写错时的报错也必须是可读中文，并且要告诉模型"有哪些可用"
    specs["bad_expr_unknown_name"] = {
        "axes": [{"series": [{"kind": "function", "expr": "foo(x)", "xrange": [0, 1]}]}],
        "expect_error": True, "error_contains": "不支持的名称",
    }
    specs["bad_expr_syntax"] = {
        "axes": [{"series": [{"kind": "function", "expr": "x**", "xrange": [0, 1]}]}],
        "expect_error": True, "error_contains": "语法",
    }
    specs["bad_graph_missing_node"] = {
        "axes": [{"series": [{"kind": "graph", "nodes": [{"id": "A", "x": 0, "y": 0}],
                              "edges": [{"from": "A", "to": "Z"}]}]}],
        "expect_error": True, "error_contains": "节点",
    }
    return specs


def colored_bbox(path, hex_color, tol=52):
    """量测某种颜色像素的外接框（用来验证等比例：圆的外接框必须是正方形）。"""
    from PIL import Image
    import numpy as np

    with Image.open(path) as image:
        arr = np.asarray(image.convert("RGB"), dtype=np.int16)
    target = np.array([int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)],
                      dtype=np.int16)
    mask = np.abs(arr - target).max(axis=2) <= tol
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return None
    return {"width": int(xs.max() - xs.min() + 1), "height": int(ys.max() - ys.min() + 1),
            "pixels": int(xs.size)}


def circle_ratio(path, hex_color):
    box = colored_bbox(path, hex_color)
    if not box:
        return None, "没有找到 %s 的像素" % hex_color
    if box["height"] <= 0:
        return None, "外接框高度为 0"
    return (box["width"] / float(box["height"]),
            "%dx%d px / %d 像素" % (box["width"], box["height"], box["pixels"]))


def run_case(python, name, spec, out_dir, backend):
    spec = dict(spec)
    checks = {key: spec.pop(key) for key in CASE_KEYS if key in spec}
    path = os.path.join(out_dir, "%s.png" % name)
    spec["out"] = path
    spec.setdefault("width", 1000)
    spec.setdefault("height", 620)
    spec.setdefault("dpi_scale", 1.4)
    fd, spec_path = tempfile.mkstemp(suffix=".json", text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(spec, handle, ensure_ascii=False)
    try:
        proc = subprocess.run([python, FIGURE, "--spec", spec_path], capture_output=True, timeout=300)
        stdout = proc.stdout.decode("utf-8", "replace").strip()
        stderr = proc.stderr.decode("utf-8", "replace").strip()
    finally:
        os.unlink(spec_path)
    record = {"case": name, "png": path, "exit": proc.returncode}
    try:
        payload = json.loads(stdout)
    except Exception:
        record.update({"pass": False,
                       "why": "stdout 不是合法 JSON：%s" % ((stdout or stderr)[:200])})
        return record

    if checks.get("expect_error"):
        error = payload.get("error") or ""
        problems = []
        if payload.get("ok") is not False:
            problems.append("预期失败，实际 ok=%r" % payload.get("ok"))
        if proc.returncode != 1:
            problems.append("退出码 %s（预期 1）" % proc.returncode)
        if not isinstance(error, str) or len(error.strip()) < 3:
            problems.append("错误信息不可读：%r" % error)
        elif not any("\u4e00" <= ch <= "\u9fff" for ch in error):
            problems.append("错误信息里没有中文：%r" % error)
        needle = checks.get("error_contains")
        if needle and needle not in error:
            problems.append("错误信息未包含 %r：%s" % (needle, error))
        record.update({"pass": not problems, "why": "；".join(problems), "error": error[:160]})
        return record

    problems = []
    if payload.get("ok") is not True:
        problems.append("ok 不是 true：%s" % payload.get("error"))
    if proc.returncode != 0:
        problems.append("退出码 %s" % proc.returncode)
    if not os.path.exists(path):
        problems.append("文件未落盘")
    elif os.path.getsize(path) < 1500:
        problems.append("文件过小：%s 字节（< 1500）" % os.path.getsize(path))
    if payload.get("backend") != backend:
        problems.append("后端是 %s，期望 %s" % (payload.get("backend"), backend))
    unsupported = payload.get("unsupported") or []
    kinds = payload.get("kinds") or []
    presets = payload.get("presets") or []
    for kind in checks.get("expect_kinds") or []:
        if kind not in kinds:
            problems.append("kinds 里没有 %s（实际 %s）" % (kind, kinds))
    for kind in checks.get("expect_supported") or []:
        if any(kind in item for item in unsupported):
            problems.append("%s 被列为不支持：%s" % (kind, unsupported))
    for needle in checks.get("expect_unsupported") or []:
        if not any(needle in item for item in unsupported):
            problems.append("unsupported 里缺少 %r（实际 %s）" % (needle, unsupported))
    if checks.get("expect_no_unsupported") and unsupported:
        problems.append("该用例本应完全支持，却报告 unsupported：%s" % unsupported)
    for wanted in checks.get("expect_presets") or []:
        if wanted not in presets:
            problems.append("presets 里没有 %s（实际 %s）" % (wanted, presets))
    for warning in payload.get("warnings") or []:
        if "异常放大" in warning:
            problems.append("出现画布异常放大告警：%s" % warning)

    ratio_text = None
    color = checks.get("check_circle") or checks.get("measure_color")
    if color and os.path.exists(path):
        ratio, detail = circle_ratio(path, color)
        ratio_text = None if ratio is None else "%.3f（%s）" % (ratio, detail)
        if checks.get("check_circle"):
            if ratio is None:
                problems.append("等比例校验失败：%s" % detail)
            elif not (0.93 <= ratio <= 1.07):
                problems.append("等比例校验失败：外接框宽高比 %.3f 不接近 1（%s）" % (ratio, detail))

    record.update({
        "pass": not problems, "why": "；".join(problems), "backend": payload.get("backend"),
        "bytes": payload.get("bytes"), "size": "%sx%s" % (payload.get("width"), payload.get("height")),
        "kinds": kinds, "presets": presets, "unsupported": unsupported,
        "warnings": payload.get("warnings"), "circle_ratio": ratio_text,
    })
    return record


def main(argv):
    python = sys.executable
    out_dir = os.path.join(HERE, "out", "presets")
    only = None
    expect_backend = None
    force_backend = None
    index = 0
    while index < len(argv):
        if argv[index] == "--python" and index + 1 < len(argv):
            python = argv[index + 1]
            index += 2
        elif argv[index] == "--out" and index + 1 < len(argv):
            out_dir = argv[index + 1]
            index += 2
        elif argv[index] == "--only" and index + 1 < len(argv):
            only = argv[index + 1]
            index += 2
        elif argv[index] == "--expect-backend" and index + 1 < len(argv):
            expect_backend = argv[index + 1]
            index += 2
        elif argv[index] == "--force-backend" and index + 1 < len(argv):
            force_backend = argv[index + 1]
            index += 2
            index += 2
        else:
            index += 1
    os.makedirs(out_dir, exist_ok=True)

    # 强制后端时，期望后端与用例期望都跟着走：
    # 否则会出现"spec 被钉成 pillow，用例却按 matplotlib 生成期望"的自相矛盾（实测踩到过）。
    if force_backend and not expect_backend:
        expect_backend = force_backend

    has_matplotlib = True
    try:
        import matplotlib  # noqa: F401
    except Exception:
        has_matplotlib = False
    backend = expect_backend or ("matplotlib" if has_matplotlib else "pillow")

    result = {"python": python, "matplotlib": has_matplotlib, "backend": backend,
              "out_dir": os.path.abspath(out_dir), "cases": []}
    for name, spec in cases(backend).items():
        if only and not name.startswith(only):
            continue
        # --force-backend：把 spec.backend 钉死后端。这是**用户真实会用的路径**
        # （viz_figure / mech_diagram 的 spec 里就有 backend 字段），
        # 因此比"换一个没装 matplotlib 的解释器"更贴近实际，也让 Pillow 路径能在主解释器上回归。
        if force_backend and isinstance(spec, dict):
            spec = dict(spec)
            spec["backend"] = force_backend
        result["cases"].append(run_case(python, name, spec, out_dir, backend))
    result["passed"] = sum(1 for item in result["cases"] if item["pass"])
    result["total"] = len(result["cases"])

    lines = []
    for item in result["cases"]:
        if item.get("exit") == 1 and "error" in item:
            detail = "错误路径：" + str(item.get("error", ""))[:70]
        else:
            detail = "%s %s %s" % (item.get("backend") or "-", item.get("size") or "-",
                                   "%s 字节" % item.get("bytes") if item.get("bytes") else "")
        if item.get("unsupported"):
            detail += "｜unsupported: %s" % "；".join(item["unsupported"])
        if item.get("circle_ratio"):
            detail += "｜圆外接框宽高比 %s" % item["circle_ratio"]
        if not item["pass"]:
            detail += "｜为什么失败：" + str(item.get("why"))
        lines.append("%s  %-28s %s" % ("PASS" if item["pass"] else "FAIL", item["case"], detail))
    sys.stdout.write("\n".join(lines) + "\n")
    sys.stdout.write("%d/%d passed（解释器 %s，后端 %s）\n"
                     % (result["passed"], result["total"], python, backend))

    # 新增图元的支持矩阵（支持 / 降级 / 不支持，如实列出）
    matrix = []
    for kind in NEW_KINDS:
        case_names = [item["case"] for item in result["cases"] if kind in (item.get("kinds") or [])]
        reasons = []
        for item in result["cases"]:
            for text in item.get("unsupported") or []:
                if kind in text and text not in reasons:
                    reasons.append(text)
        if reasons:
            state = "不支持/降级：" + "；".join(reasons)
        elif case_names:
            state = "支持"
        else:
            state = "未覆盖"
        matrix.append("  %-12s %-22s %s" % (kind, ",".join(case_names) or "-", state))
    sys.stdout.write("新增图元支持矩阵（后端 %s）：\n%s\n" % (backend, "\n".join(matrix)))

    sys.stdout.write("产物 PNG 绝对路径：\n")
    for item in result["cases"]:
        if item.get("exit") == 0:
            sys.stdout.write("  %s\n" % os.path.abspath(item["png"]))
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    return 0 if result["passed"] == result["total"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))