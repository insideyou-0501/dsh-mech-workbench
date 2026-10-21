#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reading_cases.py —— 「读图」环节的离线自测（subprocess 调脚本；不联网、不调用视觉模型）。

覆盖范围：
  1. probe：4 张现有测试图的字段完整性与 suggested_ops（每项必须带 why、op 名合法、能原样喂回去跑通）；
     另验证「模糊图必须被识别」「暗图必须建议提对比度」「EXIF 旋转必须建议 auto_orient」。
  2. 新能力：crop_box / zoom / binarize(otsu+adaptive) / denoise(median+bilateral) / ink /
     rotate / deskew / split(mode=auto) / perspective（显式四点 + auto_detect）各至少一例，
     产出非空 PNG 且尺寸符合预期；deskew 用「已知 -6° 旋转图」验证估计角与纠正效果。
  3. render_math：长中文、英文、含 sections+highlight 三例，在 matplotlib 路径与 Pillow 路径各跑一次；
     另跑一例超长文本验证「缩字号 / 分栏」降级确实写进了 warnings。
  4. 错误路径：坏 box、不存在的文件、坏 op 名、坏 corners、坏 crop_original、缺 out
     → 必须是 ok=false + 退出码 1 + 可读中文错误（stdout 仍是合法 JSON，不是崩）。
  5. 没有 numpy 的本机：能降级的降级、不能的给可读原因（不许崩、不许假装）。
  6. auto_orient 的方向转正：**EXIF 被 DSH 附件归一化剥掉**之后，必须靠像素判
     「文字横排/竖排」；判不出来要存疑不硬判；带 EXIF 的图仍走 EXIF 路径；
     竖排时附的「方向候选对照图」不许被当成切块（详见第 7 节注释）。

用法（**不把解释器路径写死在代码里**：默认用当前解释器，可用参数/环境变量指定）：
    <python> reading_cases.py [--out DIR] [--matplotlib-python EXE] [--pillow-python EXE]

    --pillow-python 指向"有 Pillow、没有 matplotlib"的解释器（本项目用 office-runtime 的内置
    Python）；不给也能跑——脚本会用 renderer="pillow" 强制走 Pillow 路径，并在报告里注明是
    "强制降级"还是"真 Pillow 环境"。

环境变量：MECH_MPL_PYTHON / MECH_PILLOW_PYTHON（等价于上面两个参数）。

输出：逐项 PASS/FAIL → 末尾 "N/M passed" + 所有产物 PNG 的绝对路径。
"""

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys

sys.dont_write_bytecode = True  # 别在被测的 scripts/ 目录里留 __pycache__

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat
except Exception as exc:  # pragma: no cover
    sys.stdout.write("本机 Python 缺少 Pillow，无法运行读图自测：%s\n" % exc)
    raise SystemExit(1)

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
SCRIPTS = os.path.join(PLUGIN, "scripts")
CASES = os.path.join(HERE, "cases")
DEFAULT_OUT = os.path.join(PLUGIN, "test", "out", "reading")

PREPROCESS = os.path.join(SCRIPTS, "preprocess.py")
VISION = os.path.join(SCRIPTS, "vision.py")
RENDER_MATH = os.path.join(SCRIPTS, "render_math.py")

# preprocess.py 认得的操作名（suggested_ops 里出现别的名字就是 bug）
LEGAL_OPS = {"auto_orient", "crop", "crop_box", "upscale", "grayscale", "autocontrast",
             "sharpen", "probe", "rotate", "deskew", "perspective", "zoom", "binarize",
             "denoise", "ink", "enhance_text", "split"}

PROBE_KEYS = ["width", "height", "mode", "exif_orientation", "aspect", "mean_luma",
              "contrast", "is_color", "blur_estimate", "skew_estimate", "suggested_ops"]

RESULTS = []
ARTIFACTS = []


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def record(name, ok, detail=""):
    RESULTS.append({"name": name, "ok": bool(ok), "detail": str(detail)})
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  — " + str(detail)) if detail else ""))


def run_script(python, script, spec, spec_dir=None, env=None):
    """跑一个脚本，返回 {code, data, stdout, stderr}；data 为 None 表示 stdout 不是 JSON。

    小 spec 走 `--spec-json`（顺带覆盖内联契约）；超大 spec（很长的转写文本）改用
    `--spec <文件>`——Windows 命令行长度上限约 32KB，内联会直接 WinError 206。
    """
    payload = json.dumps(spec, ensure_ascii=False)
    temp_path = None
    if len(payload) > 4000:
        directory = spec_dir or DEFAULT_OUT
        os.makedirs(directory, exist_ok=True)
        temp_path = os.path.join(directory, "spec-long.json")
        with open(temp_path, "w", encoding="utf-8") as handle:
            handle.write(payload)
        command = [python, script, "--spec", temp_path]
    else:
        command = [python, script, "--spec-json", payload]
    child_env = None
    if env:
        child_env = dict(os.environ)
        child_env.update(env)
    done = subprocess.run(command, capture_output=True, env=child_env)
    stdout = done.stdout.decode("utf-8", "replace").strip()
    try:
        data = json.loads(stdout)
    except Exception:
        data = None
    return {"code": done.returncode, "data": data, "stdout": stdout,
            "stderr": done.stderr.decode("utf-8", "replace").strip()}


def png_size(path):
    """返回 (宽, 高)；不是合法 PNG 返回 None。"""
    try:
        with Image.open(path) as handle:
            handle.load()
            return handle.size
    except Exception:
        return None


def load_module(path, name):
    """把脚本当模块装载（不执行 main），用于直接验证纯函数。"""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def register(paths):
    for path in paths:
        if path and path not in ARTIFACTS:
            ARTIFACTS.append(path)


def collect_outputs(data, main=True):
    paths = []
    if not isinstance(data, dict):
        return paths
    for candidate in [data.get("main") if main else None, data.get("out")]:
        if candidate:
            paths.append(candidate)
    for item in data.get("outputs") or []:
        if isinstance(item, dict) and item.get("path"):
            paths.append(item["path"])
    unique = []
    for path in paths:
        if path not in unique:
            unique.append(path)
    return unique


def read_error(result):
    """从结果里取出可读错误文本（取不到就退回 stdout 片段）。"""
    data = result.get("data")
    if isinstance(data, dict) and data.get("error"):
        return str(data["error"])
    return (result.get("stderr") or result.get("stdout") or "")[-200:]


def expect_error(name, result, must_contain):
    """错误路径：必须是 ok=false + exit 1 + stdout 合法 JSON + 中文原因含关键词。"""
    data = result.get("data")
    error = read_error(result)
    ok = (result["code"] == 1 and isinstance(data, dict) and data.get("ok") is False
          and any(word in error for word in must_contain))
    record(name, ok, "exit=%d error=%s" % (result["code"], error[:110]))


def highlight_pixels(path, target=(176, 0, 96), tolerance=26):
    """数一数高亮色（加粗+换色）真的画上去了多少个像素。"""
    with Image.open(path) as handle:
        image = handle.convert("RGB")
    total = 0
    palette = image.getcolors(maxcolors=1 << 24) or []
    for count, pixel in palette:
        if (abs(pixel[0] - target[0]) <= tolerance
                and abs(pixel[1] - target[1]) <= tolerance
                and abs(pixel[2] - target[2]) <= tolerance):
            total += count
    return total


def mean_abs_diff(first_path, second_image):
    """对照图 vs 原图裁切：整幅平均通道差（检验透视采样没把内容搞歪）。"""
    with Image.open(first_path) as handle:
        first = handle.convert("RGB")
    if first.size != second_image.size:
        return None
    diff = ImageChops.difference(first, second_image.convert("RGB"))
    return sum(ImageStat.Stat(diff).mean) / 3.0


# --------------------------------------------------------------------------- #
# 测试素材
# --------------------------------------------------------------------------- #
LONG_ZH = (
    "第 3 题（18 分）\n"
    "如图所示电路，已知 $U_s = 12\\,\\mathrm{V}$，$R_1 = 2\\,\\mathrm{k}\\Omega$，"
    "$R_2 = 3\\,\\mathrm{k}\\Omega$，$R_3 = 6\\,\\mathrm{k}\\Omega$，电容 $C = 10\\,\\mu\\mathrm{F}$，"
    "电感 $L = 5\\,\\mathrm{mH}$。开关 S 在 $t=0$ 时由断开变为闭合，此前电路已处于稳态。\n"
    "要求：(1) 求 $t=0^+$ 时刻各支路电流的初始值；(2) 写出电容电压 $u_C(t)$ 的微分方程并求其全响应；"
    "(3) 若要 $u_C(t)$ 在 $50\\,\\mathrm{ms}$ 内达到稳态值的 95%，$R_2$ 的取值应满足什么条件？\n"
    "【存疑】图中节点编号为 ① ② ③，电流参考方向已用箭头标出；第 (3) 问的数值 50 与手写 60 形近。\n"
)
ZH_FILLER = ("补充说明：这一段刻意拉长，用来检验中英混排的像素级自动换行、"
             "标点避头尾与超长内容的分栏（或缩字号）降级是否如实写进 warnings。")

LONG_EN = (
    "Problem 4 (20 points)\n"
    "A rigid tank contains $m = 2.5\\,\\mathrm{kg}$ of air initially at $T_1 = 300\\,\\mathrm{K}$ and "
    "$p_1 = 150\\,\\mathrm{kPa}$. The tank is heated until the pressure reaches "
    "$p_2 = 450\\,\\mathrm{kPa}$. Assume air behaves as an ideal gas with "
    "$c_v = 0.718\\,\\mathrm{kJ/(kg\\cdot K)}$ and $R = 0.287\\,\\mathrm{kJ/(kg\\cdot K)}$.\n"
    "Determine (a) the final temperature $T_2$, (b) the heat transfer $Q_{12}$, and (c) the entropy "
    "change $\\Delta S$ of the air.\n"
    "Note: the table below lists $v$ in $\\mathrm{m^3/kg}$; the handwritten value 450 is easily "
    "confused with 4500.\n"
    "Filler: supercalifragilisticexpialidocious_token_without_any_space_at_all_"
    "and_a_CJK/ASCII_mixed_run读图自检用来验证长拉丁串的硬切换行。\n"
)

SECTIONS = [
    {"heading": "已知", "lines": [
        "$P = 10\\,\\mathrm{kN}$ 作用在跨中",
        "$q = 5\\,\\mathrm{kN/m}$ 分布在 $0\\sim2\\,\\mathrm{m}$ 段",
        "梁长 $L = 4\\,\\mathrm{m}$，抗弯截面系数 $W = 120\\,\\mathrm{cm^3}$"]},
    {"heading": "求", "lines": [
        "支座反力 $R_A$、$R_B$",
        "最大弯矩 $M_{\\max}$ 及其所在截面 $x$",
        "最大正应力 $\\sigma_{\\max}$ 是否满足 $[\\sigma] = 160\\,\\mathrm{MPa}$"]},
    {"heading": "图要素", "lines": [
        "左端铰支座 A（三角形），右端滚动支座 B（三角形+圆圈）",
        "跨中向下的集中力箭头，数值 $P = 10\\,\\mathrm{kN}$",
        "左侧向上的一组均布载荷箭头，数值 $q = 5\\,\\mathrm{kN/m}$",
        "底部尺寸线：总长 $L = 4\\,\\mathrm{m}$，均布段 $0\\sim2\\,\\mathrm{m}$"]},
    {"heading": "存疑", "lines": [
        "侧视图中截面高度标注 200，与手写 20 形近（不确定是 mm 还是 cm）",
        "支座 B 下的符号可能是滚动支座，也可能是铰支座"]},
]
HIGHLIGHTS = ["10 kN", "160 MPa", "5 kN/m", "200", "20"]

BAD_CASES = [
    ("preprocess 坏 box（左上右下写反）",
     PREPROCESS,
     {"src": os.path.join(CASES, "q1_beam.png"), "out": "x.png",
      "ops": [{"op": "crop", "box": [0.9, 0.9, 0.1, 0.1]}]},
     ["过小", "裁剪"]),
    ("preprocess 源文件不存在",
     PREPROCESS,
     {"src": os.path.join(CASES, "绝对没有这张图.png"), "out": "x.png", "ops": []},
     ["不存在"]),
    ("preprocess 坏 op 名",
     PREPROCESS,
     {"src": os.path.join(CASES, "q1_beam.png"), "out": "x.png",
      "ops": [{"op": "invert_colors"}]},
     ["未知操作"]),
    ("preprocess zoom factor=0",
     PREPROCESS,
     {"src": os.path.join(CASES, "q1_beam.png"), "out": "x.png",
      "ops": [{"op": "zoom", "box": [0.1, 0.1, 0.5, 0.5], "factor": 0}]},
     ["大于 0"]),
    ("preprocess perspective 只给 3 个角点",
     PREPROCESS,
     {"src": os.path.join(CASES, "q1_beam.png"), "out": "x.png",
      "ops": [{"op": "perspective", "corners": [[0, 0], [10, 0], [10, 10]]}]},
     ["4 个"]),
    ("preprocess binarize 坏 method",
     PREPROCESS,
     {"src": os.path.join(CASES, "q1_beam.png"), "out": "x.png",
      "ops": [{"op": "binarize", "method": "kmeans"}]},
     ["otsu", "adaptive"]),
    ("vision 图片不存在",
     VISION,
     {"src": [os.path.join(CASES, "没有这张照片.png")]},
     ["不存在"]),
    ("render_math 坏 crop_original",
     RENDER_MATH,
     {"original": os.path.join(CASES, "q1_beam.png"), "transcription": "题面",
      "out": "x.png", "crop_original": [0, 0, 5]},
     ["四个数字", "crop_original"]),
    ("render_math 缺 out",
     RENDER_MATH,
     {"original": os.path.join(CASES, "q1_beam.png"), "transcription": "题面"},
     ["out"]),
]


# --------------------------------------------------------------------------- #
# 各段自测
# --------------------------------------------------------------------------- #
def check_probe(python, out_dir):
    print("\n── 1) probe：只读探测（字段 + 由实测推出的建议） ──")
    images = ["q1_beam.png", "q2_three.png", "q3_dark.png", "q4_exif_rot.jpg"]
    probes = {}
    for name in images:
        path = os.path.join(CASES, name)
        result = run_script(python, PREPROCESS, {"src": path, "ops": [{"op": "probe"}]})
        data = result.get("data") or {}
        probe = data.get("probe") if isinstance(data, dict) else None
        record("probe 字段完整：%s" % name,
               bool(data.get("ok")) and isinstance(probe, dict)
               and all(key in probe for key in PROBE_KEYS)
               and isinstance(probe.get("suggested_ops"), list) and probe["suggested_ops"],
               ("%s | 建议 %s" % (json.dumps({key: probe[key] for key in PROBE_KEYS[:-1]},
                                             ensure_ascii=False),
                                 [item.get("op") for item in probe["suggested_ops"]])
                if isinstance(probe, dict) else read_error(result))[:220])
        if isinstance(probe, dict):
            probes[name] = probe

    bad_suggestion = []
    for name, probe in probes.items():
        for item in probe.get("suggested_ops", []):
            if item.get("op") not in LEGAL_OPS or not item.get("why"):
                bad_suggestion.append("%s:%s" % (name, item))
    record("suggested_ops 每项都有合法 op 名与 why（%d 张图）" % len(probes),
           bool(probes) and not bad_suggestion, "；".join(bad_suggestion) or "全部合规")

    fourth = probes.get("q4_exif_rot.jpg", {})
    record("EXIF 方向=6 的图必须建议 auto_orient",
           fourth.get("exif_orientation") == 6
           and any(item["op"] == "auto_orient" for item in fourth.get("suggested_ops", [])),
           "exif_orientation=%s" % fourth.get("exif_orientation"))

    three = probes.get("q2_three.png", {})
    record("高宽比 3.0 的竖长图必须建议 split",
           any(item["op"] == "split" for item in three.get("suggested_ops", [])),
           "aspect=%s" % three.get("aspect"))

    dark = probes.get("q3_dark.png", {})
    record("暗图（均值灰度 < 120）必须建议 autocontrast",
           isinstance(dark.get("mean_luma"), (int, float)) and dark["mean_luma"] < 120
           and any(item["op"] == "autocontrast" for item in dark.get("suggested_ops", [])),
           "mean_luma=%s" % dark.get("mean_luma"))

    # 模糊图必须被识别出来（对比归一后的清晰度阈值是实测标定的）
    blurred_path = os.path.join(out_dir, "blurred-q1.png")
    with Image.open(os.path.join(CASES, "q1_beam.png")) as handle:
        handle.convert("RGB").filter(ImageFilter.GaussianBlur(2.0)).save(blurred_path)
    register([blurred_path])
    blurred = run_script(python, PREPROCESS, {"src": blurred_path, "ops": [{"op": "probe"}]})
    info = (blurred.get("data") or {}).get("probe") or {}
    record("模糊图必须被识别（blur_label 非「清晰」或建议 sharpen）",
           info.get("blur_label") not in (None, "清晰")
           or any(item["op"] == "sharpen" for item in info.get("suggested_ops", [])),
           "blur_estimate=%s（%s）" % (info.get("blur_estimate"), info.get("blur_label")))

    # 闭环：把 suggested_ops 原样喂回去必须能跑
    for name in ["q4_exif_rot.jpg", "q2_three.png"]:
        probe = probes.get(name)
        if not probe:
            record("suggested_ops 闭环：%s" % name, False, "没有 probe 结果")
            continue
        spec = {"src": os.path.join(CASES, name), "ops": probe["suggested_ops"],
                "out": os.path.join(out_dir, "roundtrip-%s.png" % name.split(".")[0]),
                "out_dir": os.path.join(out_dir, "roundtrip-%s-pieces" % name.split(".")[0]),
                "max_side": 2000}
        result = run_script(python, PREPROCESS, spec)
        data = result.get("data") or {}
        paths = collect_outputs(data)
        register(paths)
        record("suggested_ops 原样喂回去能跑通：%s" % name,
               bool(data.get("ok")) and bool(paths) and png_size(paths[0]) is not None,
               "输出 %d 个：%s" % (len(paths), [os.path.basename(item) for item in paths])
               if data.get("ok") else read_error(result))
    return probes


def check_ops(python, out_dir):
    print("\n── 2) 新增 op：crop_box / zoom / binarize / denoise / ink / rotate / deskew / "
          "split(auto) / perspective ──")
    source = os.path.join(CASES, "q1_beam.png")
    dark = os.path.join(CASES, "q3_dark.png")

    # 2.1 crop_box + zoom（另存放大图）+ binarize(otsu) + denoise(median)
    zoom_out = os.path.join(out_dir, "zoom-region.png")
    main_out = os.path.join(out_dir, "zoom-binarize-main.png")
    spec = {"src": source, "out": main_out, "ops": [
        {"op": "crop_box", "box": [0.06, 0.12, 0.62, 0.62]},
        {"op": "zoom", "box": [0.0, 0.0, 1.0, 1.0], "factor": 3, "out": zoom_out},
        {"op": "binarize", "method": "otsu"},
        {"op": "denoise", "method": "median", "size": 3},
    ], "max_side": 2000}
    result = run_script(python, PREPROCESS, spec)
    data = result.get("data") or {}
    crops = [item for item in (data.get("outputs") or []) if item.get("kind") == "zoom"]
    main_size = png_size(main_out)
    zoom_size = png_size(zoom_out)
    register([main_out, zoom_out])
    record("crop_box + zoom + binarize(otsu) + denoise(median) 全部落盘",
           bool(data.get("ok")) and main_size and zoom_size and bool(crops),
           "main=%s zoom=%s | notes=%d 条" % (main_size, zoom_size, len(data.get("notes") or []))
           if data.get("ok") else read_error(result))
    # 裁切区域 0.06~0.62 × 0.12~0.62 of 1200x900 → 672x450，×3 = 2016x1350
    record("zoom 放大倍数与尺寸符合预期（672x450 ×3 → 2016x1350）",
           zoom_size == (2016, 1350), "zoom_size=%s" % (zoom_size,))
    record("max_side=2000 被遵守（主图最长边 ≤ 2000）",
           bool(main_size) and max(main_size) <= 2000, "main=%s" % (main_size,))

    # 2.2 rotate：90° + expand → 尺寸精确转置
    rotate_out = os.path.join(out_dir, "rotate90.png")
    result = run_script(python, PREPROCESS, {
        "src": source, "out": rotate_out, "ops": [{"op": "rotate", "angle": 90}]})
    data = result.get("data") or {}
    register([rotate_out])
    record("rotate(angle=90, expand) 尺寸正确（1200x900 → 900x1200）",
           bool(data.get("ok")) and png_size(rotate_out) == (900, 1200),
           "size=%s" % (png_size(rotate_out),))

    # 2.3 ink + binarize(adaptive) + denoise(bilateral)
    adaptive_out = os.path.join(out_dir, "dark-ink-adaptive.png")
    result = run_script(python, PREPROCESS, {
        "src": dark, "out": adaptive_out, "ops": [
            {"op": "ink"},
            {"op": "denoise", "method": "bilateral", "radius": 2},
            {"op": "binarize", "method": "adaptive", "block": 31, "c": 6}]})
    data = result.get("data") or {}
    register([adaptive_out])
    mode = None
    if png_size(adaptive_out):
        with Image.open(adaptive_out) as handle:
            mode = handle.mode
    record("ink + denoise(bilateral) + binarize(adaptive) 产出单通道图",
           bool(data.get("ok")) and png_size(adaptive_out) is not None and mode == "L",
           "mode=%s size=%s" % (mode, png_size(adaptive_out))
           if data.get("ok") else read_error(result))

    # 2.4 deskew：已知 -6° 旋转图 → 估计角应 ≈ +6°，纠正后倾角回落
    skewed_path = os.path.join(out_dir, "skewed-minus6.png")
    with Image.open(source) as handle:
        handle.convert("RGB").rotate(-6, resample=Image.BICUBIC, expand=True,
                                     fillcolor=(255, 255, 255)).save(skewed_path)
    deskew_out = os.path.join(out_dir, "deskewed.png")
    before = run_script(python, PREPROCESS, {"src": skewed_path, "ops": [{"op": "probe"}]})
    skew_before = ((before.get("data") or {}).get("probe") or {}).get("skew_estimate")
    result = run_script(python, PREPROCESS, {"src": skewed_path, "out": deskew_out,
                                            "ops": [{"op": "deskew"}]})
    data = result.get("data") or {}
    register([skewed_path, deskew_out])
    deskew = data.get("deskew") or {}
    record("deskew 对已知 -6° 旋转图给出 ≈ +6° 的校正角并真的转了",
           bool(data.get("ok")) and deskew.get("applied") is True
           and isinstance(deskew.get("angle"), (int, float))
           and 4.5 <= abs(deskew["angle"]) <= 7.5,
           "检测输入倾角 %s° → 校正角 %s°（%s）"
           % (skew_before, deskew.get("angle"), deskew.get("detail")))
    after = run_script(python, PREPROCESS, {"src": deskew_out, "ops": [{"op": "probe"}]})
    skew_after = ((after.get("data") or {}).get("probe") or {}).get("skew_estimate")
    record("deskew 纠正后倾角回落（|纠正前| > |纠正后| 且 |纠正后| ≤ 1°）",
           isinstance(skew_before, (int, float)) and isinstance(skew_after, (int, float))
           and abs(skew_after) <= 1.0 and abs(skew_after) < abs(skew_before),
           "纠正前 %s° → 纠正后 %s°" % (skew_before, skew_after))
    record("deskew 输出尺寸合理（expand=true，不小于原图 90%）",
           png_size(deskew_out) is not None
           and png_size(deskew_out)[0] >= 0.9 * 1288 and png_size(deskew_out)[1] >= 0.9 * 1022,
           "size=%s（旋转源图 1288x1022）" % (png_size(deskew_out),))

    # 2.5 split(mode=auto)：一页三题必须切出 3 段，且每段带 index/box/path
    auto_dir = os.path.join(out_dir, "split-auto")
    result = run_script(python, PREPROCESS, {
        "src": os.path.join(CASES, "q2_three.png"),
        "split": {"mode": "auto", "axis": "y"}, "out_dir": auto_dir})
    data = result.get("data") or {}
    outputs = data.get("outputs") or []
    register([item.get("path") for item in outputs])
    shapes = [png_size(item.get("path")) for item in outputs]
    record("split(mode=auto) 把 800x2400 的一页三题切成 3 段",
           bool(data.get("ok")) and len(outputs) == 3
           and all(isinstance(item.get("index"), int) and item.get("box") and item.get("path")
                   for item in outputs),
           "段数=%d boxes=%s" % (len(outputs), [item.get("box") for item in outputs])
           if data.get("ok") else read_error(result))
    record("split 每段都是非空 PNG 且高度合理（≥ 300 px）",
           bool(shapes) and all(size and size[1] >= 300 and size[0] == 800 for size in shapes),
           "各段尺寸=%s" % (shapes,))
    record("split 各段纵向覆盖（首段从 0 起、末段到图底，且相邻不重叠过多）",
           bool(outputs) and outputs[0]["box"][1] == 0
           and outputs[-1]["box"][3] == 2400
           and all(outputs[index + 1]["box"][1] >= outputs[index]["box"][1]
                   for index in range(len(outputs) - 1)),
           "boxes=%s" % ([item.get("box") for item in outputs],))

    overlap_dir = os.path.join(out_dir, "split-auto-3-overlap")
    result = run_script(python, PREPROCESS, {
        "src": os.path.join(CASES, "q2_three.png"),
        "split": {"mode": "auto", "axis": "y", "count": 3, "overlap": 0.08},
        "out_dir": overlap_dir})
    data = result.get("data") or {}
    outputs = data.get("outputs") or []
    register([item.get("path") for item in outputs])
    boxes = [item.get("box") or [0, 0, 0, 0] for item in outputs]
    overlaps = [boxes[index + 1][1] < boxes[index][3] for index in range(len(boxes) - 1)]
    record("split(mode=auto, count=3, overlap=0.08) 恰好 3 段且相邻块有重叠",
           bool(data.get("ok")) and len(outputs) == 3 and overlaps and all(overlaps),
           "boxes=%s 重叠=%s" % (boxes, overlaps) if data.get("ok") else read_error(result))

    # 2.6 perspective：显式四点（矩形 → 应等价于直接裁切）
    rect_out = os.path.join(out_dir, "perspective-rect.png")
    corners = [[100, 80], [1100, 80], [1100, 820], [100, 820]]
    result = run_script(python, PREPROCESS, {
        "src": source, "out": rect_out,
        "ops": [{"op": "perspective", "corners": corners}]})
    data = result.get("data") or {}
    register([rect_out])
    with Image.open(source) as handle:
        reference = handle.convert("RGB").crop((100, 80, 1100, 820))
    difference = mean_abs_diff(rect_out, reference) if png_size(rect_out) else None
    record("perspective（矩形四点）尺寸正确且内容与直接裁切一致（平均通道差 < 20）",
           bool(data.get("ok")) and png_size(rect_out) == (1000, 740)
           and difference is not None and difference < 20,
           "size=%s 平均差=%.2f" % (png_size(rect_out), difference if difference is not None
                                    else -1))

    # 2.7 perspective：倾斜四点 → 必须是合法输出且不是空白
    tilt_out = os.path.join(out_dir, "perspective-tilt.png")
    result = run_script(python, PREPROCESS, {
        "src": source, "out": tilt_out,
        "ops": [{"op": "perspective",
                 "corners": [[120, 60], [1080, 120], [1140, 860], [80, 800]]}]})
    data = result.get("data") or {}
    register([tilt_out])
    size = png_size(tilt_out)
    record("perspective（倾斜四点）产出非空矫正图",
           bool(data.get("ok")) and size is not None and max(size) > 900,
           "size=%s" % (size,) if data.get("ok") else read_error(result))

    # 2.8 perspective auto_detect：合成的「深色桌面 + 白纸」照片
    paper_path = os.path.join(out_dir, "paper-on-desk.png")
    canvas = Image.new("RGB", (1400, 1100), (72, 74, 78))
    with Image.open(source) as handle:
        paper = handle.convert("RGB")
    canvas.paste(paper, (100, 100))
    canvas.save(paper_path)
    auto_out = os.path.join(out_dir, "perspective-auto.png")
    result = run_script(python, PREPROCESS, {
        "src": paper_path, "out": auto_out,
        "ops": [{"op": "perspective", "auto_detect": True}]})
    data = result.get("data") or {}
    register([paper_path, auto_out])
    size = png_size(auto_out)
    note = next((item for item in (data.get("notes") or []) if "auto_detect" in item), "")
    record("perspective auto_detect：深色桌面上的白纸能被找到并矫正成 1200x900 左右",
           bool(data.get("ok")) and size is not None
           and abs(size[0] - 1200) <= 20 and abs(size[1] - 900) <= 20,
           "size=%s | %s" % (size, note[:120]) if data.get("ok") else read_error(result))

    # 2.9 perspective auto_detect：整幅就是题图时必须如实说找不到，不许瞎裁
    result = run_script(python, PREPROCESS, {
        "src": source, "out": os.path.join(out_dir, "perspective-auto-noop.png"),
        "ops": [{"op": "perspective", "auto_detect": True}]})
    data = result.get("data") or {}
    if data.get("ok"):
        note = " ".join(data.get("notes") or [])
        ok = ("auto_detect" in note) and ("无需" in note or "整幅" in note or "没有" in note)
        detail = note[-160:]
    else:
        error = read_error(result)
        ok = any(word in error for word in ("auto_detect", "纸张", "边界"))
        detail = error[:160]
    record("perspective auto_detect 在整幅题图上如实报告（找不到就不裁，不瞎猜）", ok, detail)

    # 2.10 limits：放大到超过内存上限时必须等比缩回并如实提示
    big_path = os.path.join(out_dir, "big-3000.png")
    Image.new("L", (3000, 3000), 240).save(big_path)
    limit_out = os.path.join(out_dir, "limits-clamped.png")
    result = run_script(python, PREPROCESS, {
        "src": big_path, "out": limit_out, "max_side": 2000,
        "ops": [{"op": "upscale", "factor": 2.0}, {"op": "grayscale"}]})
    data = result.get("data") or {}
    register([big_path, limit_out])
    notes = " ".join(data.get("notes") or [])
    limits = data.get("limits") or {}
    record("limits：放大到超内存上限会等比缩回并写明原因（不只是默默缩）",
           bool(data.get("ok")) and "内存上限" in notes and limits.get("max_pixels"),
           "max_pixels=%s 提示=%s" % (limits.get("max_pixels"),
                                     [item for item in (data.get("notes") or [])
                                      if "内存上限" in item][:1]))
    record("limits：max_side 仍是最终出口约束（主图最长边 ≤ 2000）",
           bool(data.get("ok")) and png_size(limit_out) is not None
           and max(png_size(limit_out)) <= 2000,
           "size=%s" % (png_size(limit_out),))
    result = run_script(python, PREPROCESS, {
        "src": big_path, "out": os.path.join(out_dir, "should-not-exist-2.png"),
        "ops": [{"op": "upscale", "factor": 8.0}]})
    expect_error("limits：放大倍数离谱时给可读错误（不硬撑）", result, ["尺寸过大", "上限"])


def check_render(python, pillow_python, out_dir):
    print("\n── 3) render_math：任意学科长文本对照图（matplotlib 路径 + Pillow 路径） ──")
    originals = {"zh": os.path.join(CASES, "q1_beam.png"),
                 "en": os.path.join(CASES, "q1_beam.png"),
                 "sections": os.path.join(CASES, "q1_beam.png")}
    cases = [
        ("长中文（电路/微分方程 + 存疑标注）", "zh",
         {"transcription": LONG_ZH + (ZH_FILLER * 3)}),
        ("英文（热力学 + 无空格长串）", "en",
         {"transcription": LONG_EN * 2}),
        ("sections + highlight + title", "sections",
         {"title": "读题自检：材料力学第 3 题（原图 ‖ 转写）",
          "sections": SECTIONS, "highlight": HIGHLIGHTS}),
    ]

    renderers = [("matplotlib 路径", python, "matplotlib"),
                 ("Pillow 路径", pillow_python or python, "pillow")]
    for label, engine_python, forced in renderers:
        for case_name, key, extra in cases:
            out = os.path.join(out_dir, "render-%s-%s.png" % (key, forced))
            spec = {"original": originals[key], "out": out, "width": 1600, "font_size": 22,
                    "renderer": forced}
            spec.update(extra)
            result = run_script(engine_python, RENDER_MATH, spec)
            data = result.get("data") or {}
            register([out])
            warnings = data.get("warnings") or []
            expected = "matplotlib" if forced == "matplotlib" else "pillow-text"
            got = data.get("renderer")
            ok = (bool(data.get("ok")) and png_size(out) is not None
                  and png_size(out)[1] > 200 and got == expected
                  and isinstance(data.get("substitutions"), list)
                  and isinstance(warnings, list))
            detail = ("renderer=%s size=%s substitutions=%d warnings=%d"
                      % (got, png_size(out), len(data.get("substitutions") or []),
                         len(warnings)))
            if not ok and data.get("ok") is False:
                detail = read_error(result)
            elif got != expected:
                detail += "（期望 renderer=%s）" % expected
            if forced == "matplotlib" and data.get("matplotlib_available") is False:
                detail += "｜本机没有 matplotlib，已按 Pillow 降级"
                ok = bool(data.get("ok")) and png_size(out) is not None
            record("render_math %s：%s" % (label, case_name), ok, detail)
            if key == "sections":
                count = highlight_pixels(out)
                record("render_math highlight 真的画出来了（%s：加粗+换色，非下划线）" % label,
                       count > 20, "高亮色像素 %d 个" % count)

    # 超长文本：必须走「缩字号 / 分栏」降级，并写进 warnings
    long_out = os.path.join(out_dir, "render-degrade.png")
    huge = "\n".join([LONG_ZH, ZH_FILLER * 6] * 40)
    result = run_script(python, RENDER_MATH, {
        "original": os.path.join(CASES, "q1_beam.png"), "transcription": huge,
        "out": long_out, "width": 900, "font_size": 22}, spec_dir=out_dir)
    data = result.get("data") or {}
    register([long_out])
    warnings = data.get("warnings") or []
    text = " ".join(warnings)
    degraded = ("降级" in text) or ("列" in text)
    record("超长文本必须降级（缩字号 或 分栏）并如实写进 warnings",
           bool(data.get("ok")) and png_size(long_out) is not None and degraded,
           "columns=%s font_size=%s height=%s warnings=%s"
           % (data.get("columns"), data.get("font_size"), data.get("height"),
              [item[:40] for item in warnings]) if data.get("ok") else read_error(result))
    record("超长文本分栏路径可达（columns ≥ 2 或已缩小字号，且内容未截断）",
           bool(data.get("ok")) and (data.get("columns", 1) >= 2
                                     or (isinstance(data.get("font_size"), (int, float))
                                         and data["font_size"] < 22))
           and png_size(long_out) is not None,
           "columns=%s font_size=%s height=%s"
           % (data.get("columns"), data.get("font_size"), data.get("height")))


def check_errors(python, out_dir):
    print("\n── 4) 错误路径：必须是可读中文错误，而不是崩 ──")
    for name, script, spec, must_contain in BAD_CASES:
        spec = dict(spec)
        if "out" in spec and spec["out"] == "x.png":
            spec["out"] = os.path.join(out_dir, "should-not-exist.png")
        result = run_script(python, script, spec)
        expect_error(name, result, must_contain)


def check_text_cleaning():
    print("\n── 5) 文本清洗与高亮切分（直接装载 render_math.py，验证关键替换） ──")
    module = load_module(RENDER_MATH, "render_math_under_test")
    pairs = [
        (r"$C = 10\,\mu\mathrm{F}$", ["10", "\u03bcF"], "希腊字母宏紧跟字母（\\muF）不能丢"),
        (r"$M_{\max}=\frac{PL}{4}$", ["M_(max)", "PL/4"], "下标花括号与分数"),
        (r"$\sigma_{\max} \le [\sigma] = 160\,\mathrm{MPa}$",
         ["\u03c3", "\u2264", "160MPa"], "希腊字母与关系符"),
        (r"$\sqrt{2}\,\Delta T$", ["\u221a2", "\u0394"], "根号与希腊大写"),
    ]
    for text, must_have, why in pairs:
        plain = module.latex_to_plain(text)
        missing = [item for item in must_have if item not in plain]
        record("latex_to_plain：%s → %s" % (why, plain), not missing,
               "缺少 %s" % missing if missing else "")
    spans = module.highlight_spans("P = 10 kN 与 160 MPa 对照", ["10 kN", "160 MPa"])
    picked = [item for item, is_high in spans if is_high]
    record("highlight_spans 精确切出高亮子串（且不误伤相邻文字）",
           picked == ["10 kN", "160 MPa"], str(spans))
    values = module.collect_values(module.latex_to_plain(r"$W=120\,\mathrm{cm^3}$"))
    record("collect_values 能带出单位（含 cm^3 这类幂次）",
           "120 cm^3" in values, str(values))
    substitutions = module.collect_latex_substitutions(r"$\sigma_{\max}=\frac{PL}{4}$")
    record("collect_latex_substitutions 如实记录 \\sigma→σ / \\frac→a/b",
           any("\\sigma\u2192\u03c3" in item for item in substitutions)
           and any("frac" in item for item in substitutions), str(substitutions))


def check_numpy_fallback(python, out_dir):
    """本机没有 numpy 时：能降级的降级、不能的给可读原因（一律不许崩、不许假装）。

    做法是把一个"import 就报错"的 numpy.py 放进 PYTHONPATH 冒充缺失环境，全程离线。
    """
    print("\n── 6) 没有 numpy 的本机：降级要诚实 ──")
    shim_dir = os.path.join(out_dir, "nonumpy")
    os.makedirs(shim_dir, exist_ok=True)
    with open(os.path.join(shim_dir, "numpy.py"), "w", encoding="utf-8") as handle:
        handle.write("# 自测用：故意让 import numpy 失败\n"
                     "raise ImportError('自测：故意屏蔽 numpy')\n")
    env = {"PYTHONPATH": shim_dir}

    probe_out = None
    result = run_script(python, PREPROCESS,
                        {"src": os.path.join(CASES, "q1_beam.png"), "ops": [{"op": "probe"}]},
                        env=env)
    data = result.get("data") or {}
    probe_info = data.get("probe") or {}
    record("无 numpy：probe 仍能跑（纯 Pillow 实现）并给出建议",
           bool(data.get("ok")) and bool(probe_info.get("suggested_ops")),
           "blur_estimate=%s 建议=%s" % (
               probe_info.get("blur_estimate"),
               [item.get("op") for item in probe_info.get("suggested_ops", [])])
           if data.get("ok") else read_error(result))

    pure_out = os.path.join(out_dir, "nonumpy-pure.png")
    result = run_script(python, PREPROCESS,
                        {"src": os.path.join(CASES, "q1_beam.png"), "out": pure_out,
                         "ops": [{"op": "deskew"}, {"op": "binarize", "method": "otsu"},
                                 {"op": "denoise", "method": "median"}]}, env=env)
    data = result.get("data") or {}
    register([pure_out])
    record("无 numpy：deskew / binarize(otsu) / denoise(median) 照常产出",
           bool(data.get("ok")) and png_size(pure_out) is not None,
           "size=%s notes=%d 条" % (png_size(pure_out), len(data.get("notes") or []))
           if data.get("ok") else read_error(result))

    bilateral_out = os.path.join(out_dir, "nonumpy-bilateral.png")
    result = run_script(python, PREPROCESS,
                        {"src": os.path.join(CASES, "q1_beam.png"), "out": bilateral_out,
                         "ops": [{"op": "denoise", "method": "bilateral"}]}, env=env)
    data = result.get("data") or {}
    register([bilateral_out])
    notes = " ".join(data.get("notes") or [])
    record("无 numpy：双边滤波如实降级为高斯并说明副作用（不假装做了双边）",
           bool(data.get("ok")) and "numpy" in notes and "高斯" in notes,
           [item for item in (data.get("notes") or []) if "numpy" in item][:1]
           if data.get("ok") else read_error(result))

    result = run_script(python, PREPROCESS,
                        {"src": os.path.join(CASES, "q1_beam.png"),
                         "out": os.path.join(out_dir, "should-not-exist-3.png"),
                         "ops": [{"op": "perspective",
                                  "corners": [[100, 80], [1100, 80], [1100, 820], [100, 820]]}]},
                        env=env)
    expect_error("无 numpy：透视矫正必须给可读原因而不是崩", result, ["numpy"])


# --------------------------------------------------------------------------- #
# 7) auto_orient：EXIF 被剥掉之后必须靠像素判方向
#
# 这一节锁的是**线上真故障**（不是假想的边界）：
#   症状：手机拍歪的题图，`auto_orient` 回"EXIF 方向已是正向，未改动"，图仍是歪的；
#         上层模型只能自己写一遍 PIL 代码硬转，白白多花几轮。
#   根因：DSH 的附件归一化会重新编码图片并**剥掉 EXIF**——实测从附件库拿到的那张
#         真实照片 EXIF 条目数 = 0、Orientation = None。只信 EXIF 的实现必然失效，
#         而且**失效时还报"一切正常"**，这比报错更糟。
#   修复：EXIF 无信息时改用像素内容判"文字横排/竖排"；判不出来就说存疑，不许硬判。
#
# **本文件原来没抓到它**，因为旧用例只测了"EXIF 方向=6 的图必须建议 auto_orient"——
# 恰好把"EXIF 一定可用"这个坏掉的假设当成了前提。所以下面这些用例都从
# "没有 EXIF"出发。
# --------------------------------------------------------------------------- #
RE_MEASURED = re.compile(r"行向带纹=[\d.]+ 列向带纹=[\d.]+ 比值=\S+")


def make_truth_page(width=1000, height=760, lines=7):
    """合成一页「横排文字」的真值页：平直的行带，方向真值已知（不依赖真实照片）。"""
    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    top = 60
    gap = (height - 2 * top) // lines
    for index in range(lines):
        y = top + index * gap
        thickness = 18 if index == 0 else 11
        length = (width - 140) if index % 3 else int((width - 140) * 0.62)
        draw.rectangle([70, y, 70 + length, y + thickness], fill=0)
    return image


def check_orientation(python, out_dir):
    print("\n── 7) auto_orient：EXIF 被剥掉之后的方向判定 ──")
    horizontal = os.path.join(CASES, "q1_beam.png")
    vertical = os.path.join(CASES, "q5_text_rot90.png")
    exif_rot = os.path.join(CASES, "q4_exif_rot.jpg")

    # A) 横排 + 无 EXIF：不许旋转，并且必须**声明 180° 判不了**（不许说"方向正确"了事）
    out_h = os.path.join(out_dir, "orient-horizontal.png")
    result = run_script(python, PREPROCESS,
                        {"src": horizontal, "out": out_h, "ops": [{"op": "auto_orient"}]})
    data = result.get("data") or {}
    register([out_h])
    notes = " ".join(data.get("notes") or [])
    measured = RE_MEASURED.search(notes)
    record("auto_orient 横排图（无 EXIF）：判【横排】、不旋转、并声明 180° 不可判",
           bool(data.get("ok")) and png_size(out_h) == png_size(horizontal)
           and "【横排】" in notes and "180" in notes and "倒置" in notes
           and "剥掉" in notes and measured is not None,
           "%s 尺寸=%s 实测=%s" % (png_size(horizontal), png_size(out_h),
                                   measured.group(0) if measured else "（没有实测分数）")
           if data.get("ok") else read_error(result))

    # B) 竖排 + 无 EXIF（线上真实故障形状）：必须判【竖排】并按固定默认真的转正
    out_v = os.path.join(out_dir, "orient-vertical.png")
    result = run_script(python, PREPROCESS,
                        {"src": vertical, "out": out_v, "ops": [{"op": "auto_orient"}]})
    data = result.get("data") or {}
    register([out_v])
    notes = " ".join(data.get("notes") or [])
    size_in = png_size(vertical)
    size_out = png_size(out_v)
    with Image.open(vertical) as handle:
        expect_rotated = handle.rotate(90, expand=True)
    diff = mean_abs_diff(out_v, expect_rotated) if size_out else None
    record("auto_orient 竖排图（无 EXIF=剥掉 EXIF 的真实形状）：判【竖排】并真的转 +90",
           bool(data.get("ok")) and size_out == (size_in[1], size_in[0])
           and diff is not None and diff < 2.0
           and "【竖排】" in notes and "不猜" in notes
           and RE_MEASURED.search(notes) is not None,
           "%s → %s，与 rotate(+90) 的整幅平均差=%s" % (size_in, size_out, diff)
           if data.get("ok") else read_error(result))

    # C) 竖排时附对照图；对照图**不是切块**（切块才带 index/box），也不许抢 main
    outputs = data.get("outputs") or []
    sheets = [item for item in outputs if item.get("kind") == "orient-sheet"]
    sheet_path = sheets[0].get("path") if sheets else None
    record("竖排时附「方向候选对照图」：合法 PNG、不抢 main、且不带切块的 index/box",
           bool(sheets) and png_size(sheet_path) is not None
           and sheets[0].get("bytes", 0) > 1000
           and (data.get("main") or "") != (sheet_path or "")
           and "index" not in sheets[0] and "box" not in sheets[0],
           "outputs=%s main=%s" % ([item.get("kind") for item in outputs],
                                   os.path.basename(data.get("main") or ""))
           if sheets else "没有 orient-sheet 输出")

    # D) 带 EXIF 的图必须仍然走 EXIF 路径（别被内容判定顶掉），且不产生对照图
    out_e = os.path.join(out_dir, "orient-exif.png")
    result = run_script(python, PREPROCESS,
                        {"src": exif_rot, "out": out_e, "ops": [{"op": "auto_orient"}]})
    data = result.get("data") or {}
    register([out_e])
    notes = " ".join(data.get("notes") or [])
    size_e_in = png_size(exif_rot)
    record("auto_orient 带 EXIF 的图仍走 EXIF 路径（转正、不附对照图、不落到内容判定）",
           bool(data.get("ok")) and png_size(out_e) == (size_e_in[1], size_e_in[0])
           and "按 EXIF 方向" in notes
           and not any(item.get("kind") == "orient-sheet"
                       for item in (data.get("outputs") or [])),
           "%s → %s notes=%s" % (size_e_in, png_size(out_e), notes[:90])
           if data.get("ok") else read_error(result))

    # E) 180° 倒置页：像素统计量对 180° 严格不变量 → 只能判横排，但**必须说出来**
    page = make_truth_page()
    src_180 = os.path.join(out_dir, "orient-180-input.png")
    page.rotate(180).save(src_180)
    out_180 = os.path.join(out_dir, "orient-180-output.png")
    result = run_script(python, PREPROCESS,
                        {"src": src_180, "out": out_180, "ops": [{"op": "auto_orient"}]})
    data = result.get("data") or {}
    register([src_180, out_180])
    notes = " ".join(data.get("notes") or [])
    record("auto_orient 180° 倒置页：判横排（统计量是不变量），但必须显式声明不排除倒置",
           bool(data.get("ok")) and png_size(out_180) == png_size(src_180)
           and "【横排】" in notes and "不排除整页颠倒" in notes,
           notes[:120] if data.get("ok") else read_error(result))

    # F) 空白/纯色图：**没有可用墨量结构** → 必须存疑，不许硬判横排（假声明比没结论更糟）
    src_blank = os.path.join(out_dir, "orient-blank-input.png")
    Image.new("L", (600, 400), 255).save(src_blank)
    out_blank = os.path.join(out_dir, "orient-blank-output.png")
    result = run_script(python, PREPROCESS,
                        {"src": src_blank, "out": out_blank, "ops": [{"op": "auto_orient"}]})
    data = result.get("data") or {}
    register([src_blank, out_blank])
    notes = " ".join(data.get("notes") or [])
    record("auto_orient 空白图：证据不足必须【存疑】而不是硬判横排",
           bool(data.get("ok")) and png_size(out_blank) == (600, 400)
           and "【存疑" in notes and "没有可用的文字墨量结构" in notes
           and "【横排】" not in notes and "【竖排】" not in notes
           and not any(item.get("kind") == "orient-sheet"
                       for item in (data.get("outputs") or [])),
           notes[:130] if data.get("ok") else read_error(result))


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def main(argv):
    parser = argparse.ArgumentParser(description="mech-workbench「读图」环节离线自测")
    parser.add_argument("--out", default=DEFAULT_OUT, help="产物目录（默认 mech-workbench/test/out/reading）")
    parser.add_argument("--matplotlib-python", default=os.environ.get("MECH_MPL_PYTHON") or sys.executable,
                        help="带 matplotlib 的解释器（默认当前解释器）")
    parser.add_argument("--pillow-python", default=os.environ.get("MECH_PILLOW_PYTHON"),
                        help="只有 Pillow、没有 matplotlib 的解释器（不给则用 renderer=pillow 强制走该路径）")
    args = parser.parse_args(argv)

    python = args.matplotlib_python
    pillow_python = args.pillow_python
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    print("=" * 78)
    print("mech-workbench 读图环节离线自测（不联网、不调用视觉模型）")
    print("matplotlib 路径解释器：%s" % python)
    if pillow_python:
        print("Pillow-only 路径解释器：%s" % pillow_python)
    else:
        print("Pillow-only 路径解释器：未指定 → 用 renderer=\"pillow\" 在当前解释器上强制走 Pillow 路径")
    print("产物目录：%s" % out_dir)
    print("=" * 78)

    if not os.path.isfile(PREPROCESS):
        print("找不到脚本：%s" % PREPROCESS)
        return 1

    check_probe(python, out_dir)
    check_ops(python, out_dir)
    check_render(python, pillow_python, out_dir)
    check_errors(python, out_dir)
    check_text_cleaning()
    check_numpy_fallback(python, out_dir)
    check_orientation(python, out_dir)

    passed = sum(1 for item in RESULTS if item["ok"])
    total = len(RESULTS)
    print("\n" + "=" * 78)
    if passed != total:
        print("失败项：")
        for item in RESULTS:
            if not item["ok"]:
                print("  - %s：%s" % (item["name"], item["detail"]))
    print("%d/%d passed" % (passed, total))
    print("产物 PNG（绝对路径，供肉眼验收）：")
    for path in ARTIFACTS:
        exists = os.path.isfile(path)
        size = png_size(path) if exists else None
        print("  - %s%s" % (path, ("  %s" % (size,)) if size else "  [缺失/非法]"))
    print("=" * 78)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))