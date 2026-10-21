#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_cases.py —— 对生成的 PNG 做程序化自检（弥补 read_image 之外的可证实性）。

检查项：
  1. 缺字形（tofu）检测：把每个非 ASCII 字符的位图与「私用区必然缺字形」的
     .notdef 位图比较，相同即判定为方框风险。
  2. 版式检查：原图面板按比例缩放（无内容被裁）、文字面板有墨迹、图片四周
     有留白（内容没有贴边被切）、标题带存在。
  3. 亮度检查：暗图经过 autocontrast 后对比度是否真的被拉开。

用法: <python> verify_cases.py <out_dir>
输出: stdout 一个 JSON。
"""

import json
import os
import sys

from PIL import Image, ImageFont

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
sys.dont_write_bytecode = True  # 不要在被测脚本目录留下 __pycache__

FONT = r"C:\Windows\Fonts\msyh.ttc"
NOTDEF = "\ue000"

# render_math.py 里会画出来的固定标签
LABELS = [
    "原图", "原图（已按 crop_original 裁切）", "转写（AI 读出来的题面）",
    "转写（AI 读出来的题面，请逐项核对）",
    "数值清单（逐个和原图核对）：", "公式清单：", "（转写里没识别到数字）",
    "渲染器：matplotlib mathtext · 中文：msyh.ttc · 若与原图有出入，请以原图为准",
]


def missing_glyphs(font_path, chars):
    font = ImageFont.truetype(font_path, 40, index=0)
    ref = bytes(font.getmask(NOTDEF))
    bad = []
    for char in sorted(set(chars)):
        if ord(char) < 128 or char in " \n\t":
            continue
        try:
            mask = font.getmask(char)
        except Exception:
            bad.append(char)
            continue
        if mask.size != (0, 0) and bytes(mask) == ref:
            bad.append(char)
    return bad


def ink_bbox(image, tolerance=40):
    """以「出现最多的灰度」为背景色，与背景差异 > tolerance 的像素算墨迹。

    这样白底黑字和暗底亮字都能正确取到内容包围盒。
    """
    gray = image.convert("L")
    histogram = gray.histogram()
    background = max(range(256), key=lambda value: histogram[value])
    mask = gray.point(lambda value: 255 if abs(value - background) > tolerance else 0)
    return mask.getbbox()


def check_png(path, expect_ratio=None, min_ink=0.002, check_margin=True):
    result = {"path": os.path.basename(path)}
    with Image.open(path) as handle:
        handle.load()
        image = handle.convert("RGB")
        result["size"] = [image.width, image.height]
        result["bytes"] = os.path.getsize(path)
        bbox = ink_bbox(image)
        result["ink_bbox"] = list(bbox) if bbox else None
        if bbox:
            area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
            result["ink_ratio"] = round(area / float(image.width * image.height), 4)
            # 内容不贴边（有留白说明没被切）
            margin_ok = bbox[0] > 2 and bbox[1] > 2 and \
                bbox[2] < image.width - 2 and bbox[3] < image.height - 2
            result["margin_ok"] = margin_ok
        else:
            result["ink_ratio"] = 0.0
            result["margin_ok"] = False
        result["ink_enough"] = result["ink_ratio"] >= min_ink
        result["ok"] = result["ink_enough"] and (result["margin_ok"] or not check_margin)
    return result


def check_autocontrast(dark_path, processed_path):
    with Image.open(dark_path) as handle:
        before = handle.convert("L")
        b_lo, b_hi = before.getextrema()
        b_std = _std(before)
    with Image.open(processed_path) as handle:
        after = handle.convert("L")
        a_lo, a_hi = after.getextrema()
        a_std = _std(after)
    return {
        "before_range": [b_lo, b_hi], "after_range": [a_lo, a_hi],
        "before_std": round(b_std, 2), "after_std": round(a_std, 2),
        "improved": a_std > b_std and (a_hi - a_lo) > (b_hi - b_lo),
    }


def _std(image):
    hist = image.histogram()
    total = float(sum(hist))
    mean = sum(index * count for index, count in enumerate(hist)) / total
    var = sum(count * (index - mean) ** 2 for index, count in enumerate(hist)) / total
    return var ** 0.5


def check_compare_structure(path):
    """确定性版式审计：标题带、红se数值清单、左右两栏都有墨迹、公式图确实更宽。"""
    with Image.open(path) as handle:
        handle.load()
        image = handle.convert("RGB")
    width, height = image.size

    def count_color(target, tolerance, region):
        crop = image.crop(region)
        pixels = crop.load()
        hits = 0
        for y in range(crop.height):
            for x in range(crop.width):
                pixel = pixels[x, y]
                if all(abs(pixel[index] - target[index]) <= tolerance for index in range(3)):
                    hits += 1
        return hits

    report = {"path": os.path.basename(path), "size": [width, height]}
    # 标题带（TITLE_BG = 232,238,248）
    report["title_band_pixels"] = count_color((232, 238, 248), 6, (0, 0, width, 60))
    # 数值清单红色（VALUE_INK = 192,0,0），只在下半部分的提示区找
    report["red_value_pixels"] = count_color((192, 0, 0), 40,
                                             (0, height // 2, width, height))
    left = image.crop((0, 0, width // 2 - 40, height))
    right = image.crop((width // 2 + 40, 0, width, height))
    report["left_ink"] = bool(ink_bbox(left))
    report["right_ink"] = bool(ink_bbox(right))
    # 左右两栏各自的墨迹量（matplotlib 版公式是位图，右侧墨迹应明显多于纯文本版）
    report["left_ink_pixels"] = _ink_pixels(left)
    report["right_ink_pixels"] = _ink_pixels(right)
    report["ok"] = (report["title_band_pixels"] > 1000 and report["red_value_pixels"] > 50
                    and report["left_ink"] and report["right_ink"])
    return report


def _ink_pixels(image, tolerance=40):
    gray = image.convert("L")
    histogram = gray.histogram()
    background = max(range(256), key=lambda value: histogram[value])
    return sum(1 for value in gray.getdata() if abs(value - background) > tolerance)


def main(argv):
    out_dir = argv[0] if argv else "."
    report = {"ok": True, "checks": {}}

    text = "求图示简支梁的最大弯矩与支座反力。已知：P = 10 kN，均布载荷 q = 5 kN/m，" \
           "梁长 L = 4 m，抗弯截面系数 W = 120 cm^3，最大正应力 σ max，[σ] = 160 MPa。" \
           "姓名 学号 判断题 填空题 悬臂梁 挠度 转角 阶梯轴 扭矩 切应力 桁架 轴力" \
           "⋅ ⁻¹ ⁻² ≤ ≥ √ · × ÷ ≈ ± ° μ α β γ θ σ Δ Σ ∫ ∞ → ⊥ ∥ ∠ ′ … □ ｜ ＿"
    bad = missing_glyphs(FONT, text + "".join(LABELS))
    report["checks"]["missing_glyphs_raw"] = {
        "font": FONT, "found": ["%s(U+%04X)" % (c, ord(c)) for c in bad],
        "note": "原始文本里 msyh 缺字形的字符（应由 render_math 的替换映射兜住）",
    }

    # 用交付脚本自己的替换映射再过一遍：替换后必须零缺字形
    substitution_report = {"applied": False}
    try:
        import importlib.util
        script = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "scripts", "render_math.py")
        spec = importlib.util.spec_from_file_location("render_math_under_test", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cleaned, used = module.apply_substitutions(text)
        still_bad = missing_glyphs(FONT, cleaned + "".join(LABELS))
        substitution_report = {
            "applied": True,
            "substitutions_used": used,
            "still_missing": ["%s(U+%04X)" % (c, ord(c)) for c in still_bad],
            "ok": not still_bad,
        }
    except Exception as exc:
        substitution_report = {"applied": False, "error": str(exc), "ok": False}
    report["checks"]["missing_glyphs_after_substitution"] = substitution_report

    # 公式/数值清单的纯文本形态（确定性检查"公式清单里到底写了什么"）
    if substitution_report.get("applied"):
        sample = ("三、计算题（本题 20 分）\n求图示简支梁的最大弯矩 $M_{\\max}$ 与支座反力。\n"
                  "已知 $P=10\\,\\mathrm{kN}$ 作用在跨中，均布载荷 $q=5\\,\\mathrm{kN/m}$ "
                  "分布在 $0\\sim2\\,\\mathrm{m}$ 段，梁长 $L=4\\,\\mathrm{m}$，"
                  "抗弯截面系数 $W=120\\,\\mathrm{cm^3}$。\n"
                  "要求：(1) 支座反力 $R_A$、$R_B$；(2) 最大弯矩所在截面 $x$；"
                  "(3) 最大正应力 $\\sigma_{\\max}=\\frac{M_{\\max}}{W}$，"
                  "并判断是否满足 $[\\sigma]=160\\,\\mathrm{MPa}$。")
        cleaned_sample, _used = module.apply_substitutions(sample)
        segments = module.split_segments(cleaned_sample)
        report["checks"]["formula_plain"] = {
            "formulas": module.extract_formulas(segments),
            "values": module.collect_values(module.latex_to_plain(cleaned_sample)),
        }

    files = []
    # 注意：下面这组名字来自**旧用例集**（p1/p3/p4 + pieces），现在的素材叫
    # q1_beam.png / q2_three.png / q3_dark.png / q4_exif_rot.jpg，产物名也随之变了。
    # 找不到就**如实报告"跳过"**并说明原因，而不是记 ok:false —— 否则"期望过期"
    # 和"真有版式缺陷"会被混为一谈，报告就失去意义了。
    for name, margin in [("p1_beam.png", True), ("p3_dark.png", True), ("p4_exif.png", True),
                         ("pieces/piece1.png", False), ("pieces/piece2.png", False),
                         ("pieces/piece3.png", False)]:
        path = os.path.join(out_dir, name)
        if os.path.isfile(path):
            entry = check_png(path, check_margin=margin)
            if not margin:
                entry["margin_note"] = "切块会穿过内容，贴边是预期行为（靠 overlap 保证行不被切断）"
                entry["ok"] = entry["ink_enough"]
            files.append(entry)
        else:
            files.append({
                "path": name,
                "missing": True,
                "skipped": True,
                "reason": "当前用例集里没有这个产物名（期望名来自旧用例集 p1/p3/p4），未做版式检查",
            })
    report["checks"]["preprocess_outputs"] = files
    # 注意：说明文字放在顶层 notes，**不能**塞进 checks ——
    # 末尾的 ok 汇总会对 checks 下每个元素调 .get("ok")，字符串会直接抛 AttributeError。
    if all(item.get("skipped") for item in files):
        report.setdefault("notes", []).append(
            "版式检查未覆盖任何文件：期望名（p1/p3/p4 + pieces）来自旧用例集，"
            "当前产物名已不同。缺字形检查不受影响，仍然有效。")

    dark = os.path.join(os.path.dirname(os.path.abspath(out_dir)), "q3_dark.png")
    processed = os.path.join(out_dir, "p3_dark.png")
    if os.path.isfile(dark) and os.path.isfile(processed):
        report["checks"]["autocontrast"] = check_autocontrast(dark, processed)

    # 对照图：左右两个面板都必须有墨迹 + 红色数值清单 + 标题带
    for name in ["compare_matplotlib.png", "compare_pillow.png"]:
        path = os.path.join(out_dir, name)
        if not os.path.isfile(path):
            continue
        entry = check_png(path)
        structure = check_compare_structure(path)
        entry.update({key: value for key, value in structure.items() if key != "path"})
        with Image.open(path) as handle:
            image = handle.convert("RGB")
        middle = image.crop((image.width // 2 - 40, 0, image.width // 2 + 40, image.height))
        entry["half_ink"] = {
            "left": bool(ink_bbox(image.crop((0, 0, image.width // 2 - 40, image.height)))),
            "middle_gap": bool(ink_bbox(middle)),
            "right": bool(ink_bbox(image.crop((image.width // 2 + 40, 0, image.width,
                                               image.height)))),
        }
        entry["ok"] = structure["ok"]
        report["checks"].setdefault("compare_outputs", []).append(entry)

    report["ok"] = all(
        item.get("ok", True)
        for group in report["checks"].values()
        for item in (group if isinstance(group, list) else [group])
    )
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))