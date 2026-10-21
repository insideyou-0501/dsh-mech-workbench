#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""figure.py 的覆盖矩阵自测。

用法：
    <python> viz_cases.py [--python <解释器>] [--out <目录>] [--only <名称前缀>]

对每个样例：生成 spec → 子进程调用 figure.py → 校验退出码、JSON 契约、文件是否真的落盘、
（可选）期望的后端与 unsupported。stdout 恰好一个 JSON 汇总。
"""

import json
import math
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


def grid(xs, ys, fn):
    return [[fn(x, y) for x in xs] for y in ys]


def cases():
    xs = [i * 0.2 for i in range(31)]
    ys = [j * 0.3 for j in range(21)]
    t = [i * 0.05 for i in range(126)]
    specs = {}

    specs["function"] = {
        "title": "函数曲线", "subtitle": "数学分析常用",
        "axes": [{
            "xlabel": "x", "ylabel": "f(x)", "legend": True,
            "series": [
                {"kind": "function", "expr": "x**2*sin(x)", "xrange": [-6, 6], "label": "x²sin x"},
                {"kind": "function", "expr": "exp(-x**2/8)*3", "xrange": [-6, 6], "label": "3e^{-x²/8}",
                 "color": "#e0603c"},
                {"kind": "hline", "y": 0, "style": "-", "color": "#888888"},
            ],
        }],
        "note": ["表达式走受限白名单求值（sin/cos/exp/log/sqrt…）"],
    }

    specs["loglog"] = {
        "title": "双对数坐标",
        "axes": [{
            "type": "loglog", "xlabel": "Re", "ylabel": "|H|",
            "series": [
                {"kind": "line", "x": [1, 10, 100, 1000], "y": [1.0, 0.1, 0.01, 0.001], "label": "1/Re",
                 "marker": "o"},
                {"kind": "errorbar", "x": [1, 10, 100], "y": [2.0, 0.2, 0.02], "yerr": [0.2, 0.02, 0.002],
                 "label": "实测"},
            ],
        }],
    }

    specs["polar"] = {
        "title": "极坐标",
        "axes": [{
            "type": "polar",
            "series": [{"kind": "parametric", "fx": "t", "fy": "2*cos(3*t)+1.5", "trange": [0, 6.283185307],
                        "label": "r=2cos3θ+1.5"}],
        }],
    }

    zc = grid(xs, ys, lambda x, y: math.sin(x) * math.cos(y))
    specs["contour"] = {
        "title": "等高线（应力场/二元函数）",
        "axes": [{
            "xlabel": "x", "ylabel": "y", "aspect": "equal",
            "series": [{"kind": "contour", "x": xs, "y": ys, "z": zc, "levels": 14, "cmap": "coolwarm",
                        "filled": True, "colorbar": True}],
        }],
    }

    specs["heatmap"] = {
        "title": "热力图",
        "axes": [{
            "xlabel": "列", "ylabel": "行",
            "series": [{"kind": "heatmap", "z": zc, "cmap": "viridis", "colorbar": True,
                        "x": [xs[0], xs[-1]], "y": [ys[0], ys[-1]]}],
        }],
    }

    specs["surface3d"] = {
        "title": "三维曲面",
        "axes": [{
            "xlabel": "x", "ylabel": "y", "zlabel": "z",
            "series": [{"kind": "surface3d", "x": xs, "y": ys, "z": zc, "cmap": "plasma",
                        "view": [28, -60]}],
        }],
    }

    specs["multipanel"] = {
        "title": "多子图共享 x 轴",
        "grid": {"rows": 2, "cols": 1},
        "axes": [
            {"slot": [0, 0], "title": "剪力图", "ylabel": "Q (kN)",
             "series": [{"kind": "step", "x": [0, 2, 4], "y": [5, -5, -5], "label": "Q"}]},
            {"slot": [1, 0], "title": "弯矩图", "xlabel": "x (m)", "ylabel": "M (kN·m)",
             "series": [{"kind": "line", "x": [0, 2, 4], "y": [0, 10, 0], "label": "M", "color": "#e0603c"},
                        {"kind": "area", "x": [0, 2, 4], "y": [0, 10, 0], "color": "#e0603c", "alpha": 0.2}]},
        ],
    }

    specs["schematic"] = {
        "title": "受力图与图元层",
        "axes": [{
            "xlim": [-2, 8], "ylim": [-3, 5], "aspect": "equal", "xlabel": "x", "ylabel": "y",
            "series": [
                {"kind": "polygon", "xy": [[0, 0], [6, 0], [6, 0.4], [0, 0.4]], "facecolor": "#cfd8dc",
                 "color": "#37474f", "label": "梁"},
                {"kind": "circle", "center": [0, 0.2], "r": 0.35, "color": "#2f6fbf"},
                {"kind": "circle", "center": [6, 0.2], "r": 0.35, "color": "#2f6fbf"},
                {"kind": "arrow", "xy": [3, 0.4], "xytext": [3, 3], "color": "#c0392b", "width": 2},
                {"kind": "text", "xy": [3.2, 2.2], "text": "P = 10 kN", "size": 10, "ha": "left"},
                {"kind": "annotate", "xy": [3, 0.4], "text": "跨中集中力", "xytext": [60, -40],
                 "color": "#c0392b"},
                {"kind": "vline", "x": 3, "style": ":", "color": "#9e9e9e"},
                {"kind": "text", "xy": [1.5, -1.2], "text": "A", "size": 11},
                {"kind": "text", "xy": [4.5, -1.2], "text": "B", "size": 11},
            ],
        }],
    }

    specs["bar_scatter"] = {
        "title": "数据图",
        "axes": [{
            "xlabel": "组", "ylabel": "值", "legend": True,
            "series": [
                {"kind": "bar", "x": [1, 2, 3, 4], "y": [12, 18, 9, 15], "label": "实测", "color": "#3f9a5a"},
                {"kind": "scatter", "x": [1, 2, 3, 4], "y": [13, 16, 11, 14], "label": "仿真", "color": "#c0392b"},
                {"kind": "hline", "y": 13.5, "label": "均值", "color": "#546e7a"},
            ],
        }],
    }

    specs["bad_kind"] = {
        "axes": [{"series": [{"kind": "spiral", "y": [1, 2]}]}],
        "expect_error": True,
    }
    return specs


def run_case(python, name, spec, out_dir, expect_backend=None):
    path = os.path.join(out_dir, "%s.png" % name)
    spec = dict(spec)
    expect_error = spec.pop("expect_error", False)
    spec["out"] = path
    if "title" not in spec:
        spec["title"] = name
    fd, spec_path = tempfile.mkstemp(suffix=".json", text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(spec, handle, ensure_ascii=False)
    try:
        proc = subprocess.run([python, FIGURE, "--spec", spec_path], capture_output=True, timeout=180)
        stdout = proc.stdout.decode("utf-8", "replace").strip()
        stderr = proc.stderr.decode("utf-8", "replace").strip()
    finally:
        os.unlink(spec_path)
    record = {"case": name, "exit": proc.returncode}
    try:
        payload = json.loads(stdout)
    except Exception:
        record.update({"pass": False, "why": "stdout 不是合法 JSON", "stdout": stdout[:300],
                       "stderr": stderr[:300]})
        return record
    if expect_error:
        ok = payload.get("ok") is False and proc.returncode == 1 and isinstance(payload.get("error"), str)
        record.update({"pass": ok, "why": payload.get("error", "")[:120]})
        return record
    problems = []
    if payload.get("ok") is not True:
        problems.append("ok 不是 true：%s" % payload.get("error"))
    if proc.returncode != 0:
        problems.append("退出码 %s" % proc.returncode)
    if not os.path.exists(path):
        problems.append("文件未落盘")
    elif os.path.getsize(path) < 3000:
        problems.append("文件过小：%s 字节" % os.path.getsize(path))
    if expect_backend and payload.get("backend") != expect_backend:
        problems.append("后端是 %s，期望 %s" % (payload.get("backend"), expect_backend))
    record.update({
        "pass": not problems, "why": "；".join(problems),
        "backend": payload.get("backend"), "bytes": payload.get("bytes"),
        "size": "%sx%s" % (payload.get("width"), payload.get("height")),
        "kinds": payload.get("kinds"), "unsupported": payload.get("unsupported"),
        "warnings": payload.get("warnings"),
    })
    return record


def main(argv):
    python = sys.executable
    out_dir = os.path.join(HERE, "out", "viz")
    only = None
    expect_backend = None
    i = 0
    while i < len(argv):
        if argv[i] == "--python" and i + 1 < len(argv):
            python = argv[i + 1]
            i += 2
        elif argv[i] == "--out" and i + 1 < len(argv):
            out_dir = argv[i + 1]
            i += 2
        elif argv[i] == "--only" and i + 1 < len(argv):
            only = argv[i + 1]
            i += 2
        elif argv[i] == "--expect-backend" and i + 1 < len(argv):
            expect_backend = argv[i + 1]
            i += 2
        else:
            i += 1
    os.makedirs(out_dir, exist_ok=True)

    has_matplotlib = True
    try:
        import matplotlib  # noqa: F401
    except Exception:
        has_matplotlib = False
    if expect_backend is None:
        expect_backend = "matplotlib" if has_matplotlib else "pillow"

    result = {"python": python, "matplotlib": has_matplotlib, "expected_backend": expect_backend,
              "cases": []}
    for name, spec in cases().items():
        if only and not name.startswith(only):
            continue
        result["cases"].append(run_case(python, name, spec, out_dir, expect_backend))
    result["passed"] = sum(1 for item in result["cases"] if item["pass"])
    result["total"] = len(result["cases"])
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 0 if result["passed"] == result["total"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))