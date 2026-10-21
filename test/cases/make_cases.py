#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_cases.py —— 生成 mech-workbench 的测试样例图（不含任何真实私人照片）。

产物（都在本目录下）：
    q1_beam.png        4:3 白底黑字题图：中文 + 公式（正常拍摄质量）
    q2_three.png       800x2400 竖长图：一页三题（用于 split 验证）
    q3_dark.png        很暗的题图（用于 autocontrast/grayscale 验证）
    q4_exif_rot.jpg    带 EXIF Orientation=6 的横向照片（用于 auto_orient 验证）
    q5_text_rot90.png  **文字竖排且没有 EXIF** 的题图（用于 auto_orient 的内容判方向验证）

用法：
    <python> make_cases.py
"""

import os
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))

FONT_CANDIDATES = [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
                   r"C:\Windows\Fonts\simsun.ttc"]


def font(size):
    for path in FONT_CANDIDATES:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size, index=0)
            except Exception:
                pass
    return ImageFont.load_default()


def make_q1():
    """4:3 白底黑字，中文 + 公式。"""
    width, height = 1200, 900
    image = Image.new("RGB", (width, height), (252, 252, 250))
    draw = ImageDraw.Draw(image)
    title = font(46)
    body = font(34)
    small = font(28)

    draw.text((70, 60), "三、计算题（本题 20 分）", font=title, fill=(10, 10, 10))
    lines = [
        "求图示简支梁的最大弯矩 M max 与支座反力。",
        "已知：P = 10 kN，作用在跨中；均布载荷 q = 5 kN/m，",
        "分布在 0 ~ 2 m 段；梁长 L = 4 m，抗弯截面系数 W = 120 cm^3。",
        "要求：(1) 支座反力 R A、R B；(2) 最大弯矩所在截面 x；",
        "(3) 最大正应力 σ max，并判断是否满足 [σ ] = 160 MPa。",
    ]
    y = 170
    for line in lines:
        draw.text((70, y), line, font=body, fill=(20, 20, 20))
        y += 62

    # 简支梁示意图
    bx0, by0, bx1, by1 = 130, y + 60, 1050, y + 240
    draw.line([(bx0, by0 + 60), (bx1, by0 + 60)], fill=(0, 0, 0), width=6)
    draw.polygon([(bx0, by0 + 60), (bx0 + 26, by0 + 34), (bx0 + 26, by0 + 86)],
                 outline=(0, 0, 0), fill=(255, 255, 255))
    draw.polygon([(bx1, by0 + 60), (bx1 - 26, by0 + 34), (bx1 - 26, by0 + 86)],
                 outline=(0, 0, 0), fill=(255, 255, 255))
    for index in range(9):  # 均布载荷箭头
        x = bx0 + 20 + index * 40
        draw.line([(x, by0 - 40), (x, by0 + 50)], fill=(0, 0, 0), width=3)
        draw.polygon([(x, by0 + 60), (x - 9, by0 + 42), (x + 9, by0 + 42)], fill=(0, 0, 0))
    draw.line([(bx0, by0 - 52), (bx1, by0 - 52)], fill=(0, 0, 0), width=2)
    draw.text((560, by0 - 88), "q = 5 kN/m", font=small, fill=(0, 0, 0))
    draw.line([(560, by0), (560, by0 + 50)], fill=(200, 0, 0), width=4)
    draw.text((590, by0 + 4), "P = 10 kN", font=small, fill=(200, 0, 0))
    draw.text((bx0 + 90, by0 + 66), "A", font=small, fill=(0, 0, 0))
    draw.text((bx1 - 110, by0 + 66), "B", font=small, fill=(0, 0, 0))
    draw.text((520, by1), "L = 4 m", font=small, fill=(0, 0, 0))
    draw.text((70, height - 70), "姓名：＿＿＿＿＿＿    学号：＿＿＿＿＿＿", font=small,
              fill=(40, 40, 40))
    image.save(os.path.join(HERE, "q1_beam.png"))
    return "q1_beam.png"


def make_q2():
    """一页三题的竖长图（800x2400），用于 split。"""
    width, height = 800, 2400
    image = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    title = font(38)
    body = font(27)
    problems = [
        ("第 1 题", [
            "图示悬臂梁自由端受集中力 P = 8 kN，",
            "梁长 L = 2 m，EI = 常数。",
            "求自由端挠度 w 与转角 θ。",
        ]),
        ("第 2 题", [
            "阶梯轴受扭矩 T = 300 N·m，",
            "直径 d = 40 mm，材料许用切应力 [τ] = 60 MPa。",
            "校核轴的扭转强度。",
        ]),
        ("第 3 题", [
            "平面桁架节点受力如图，各杆 EA = 常数，",
            "斜杆与水平方向夹角 α = 45°，",
            "求杆 1、杆 2 的轴力 N1、N2。",
        ]),
    ]
    y = 90
    for heading, lines in problems:
        draw.text((60, y), heading, font=title, fill=(0, 0, 0))
        y += 62
        for line in lines:
            draw.text((80, y), line, font=body, fill=(20, 20, 20))
            y += 46
        draw.rectangle([60, y + 20, width - 60, y + 300], outline=(120, 120, 120), width=3)
        draw.line([(120, y + 240), (width - 160, y + 240)], fill=(0, 0, 0), width=5)
        draw.text((150, y + 300 + 8), "（此处为示意图）", font=body, fill=(90, 90, 90))
        y += 420
    image.save(os.path.join(HERE, "q2_three.png"))
    return "q2_three.png"


def make_q3():
    """很暗的图：验证 autocontrast / grayscale。"""
    width, height = 1000, 750
    image = Image.new("RGB", (width, height), (26, 26, 30))
    draw = ImageDraw.Draw(image)
    title = font(40)
    body = font(30)
    draw.text((60, 60), "四、判断题（每题 5 分）", font=title, fill=(72, 72, 78))
    lines = [
        "1. 静定结构的内力与刚度 EI 无关。",
        "2. 梁的挠曲线近似微分方程要求小变形。",
        "3. 圆轴扭转时横截面上的切应力沿半径线性分布。",
        "4. 压杆的临界力与杆长的平方成反比。",
        "已知 G = 80 GPa，E = 200 GPa，试求泊松比 μ。",
    ]
    y = 170
    for line in lines:
        draw.text((60, y), line, font=body, fill=(84, 84, 90))
        y += 90
    image.save(os.path.join(HERE, "q3_dark.png"))
    return "q3_dark.png"


def make_q4():
    """EXIF Orientation=6：像素是横躺的，正确实现应转正为竖图。"""
    width, height = 1200, 900
    image = Image.new("RGB", (width, height), (250, 250, 246))
    draw = ImageDraw.Draw(image)
    title = font(44)
    body = font(32)
    draw.text((70, 70), "五、填空题", font=title, fill=(0, 0, 0))
    for index, line in enumerate([
        "1. 圆截面极惯性矩 I p = ______ 。",
        "2. 梁的弯矩与剪力关系 dM/dx = ______ 。",
        "3. 拉压杆的胡克定律 Δl = ______ 。",
    ]):
        draw.text((70, 200 + index * 90), line, font=body, fill=(20, 20, 20))
    draw.rectangle([70, 560, 700, 800], outline=(0, 0, 0), width=4)
    draw.text((110, 650), "（画受力图）", font=body, fill=(60, 60, 60))

    exif = Image.Exif()
    exif[0x0112] = 6  # Rotate 90 CW
    exif[0x010F] = "mech-workbench test"
    image.save(os.path.join(HERE, "q4_exif_rot.jpg"), quality=92, exif=exif)
    return "q4_exif_rot.jpg"


def make_q5():
    """**文字竖排 + 完全无 EXIF** 的题图。

    这是线上真实故障的形状：手机拍歪 + DSH 附件归一化重新编码 → EXIF 被剥掉，
    于是 `ImageOps.exif_transpose` 拿不到任何方向信息（实测 EXIF 条目数 = 0）。
    只靠 EXIF 的实现会回一句"EXIF 方向已是正向，未改动"，图其实还是歪的。
    这里保存成 PNG（PNG 路径同样不带方向信息），锁住"必须靠像素判方向"。
    """
    width, height = 1000, 760
    image = Image.new("RGB", (width, height), (253, 253, 251))
    draw = ImageDraw.Draw(image)
    title = font(40)
    body = font(29)
    draw.text((70, 60), "六、作图题（本题 15 分）", font=title, fill=(8, 8, 8))
    lines = [
        "画出图示外伸梁的剪力图与弯矩图。",
        "已知：P = 12 kN，a = 1.5 m，q = 4 kN/m，",
        "梁的弯曲刚度 EI = 常数，[σ ] = 140 MPa。",
        "要求：标注各控制截面的 Q、M 数值。",
    ]
    y = 160
    for line in lines:
        draw.text((70, y), line, font=body, fill=(18, 18, 18))
        y += 56
    draw.line([(150, y + 90), (880, y + 90)], fill=(0, 0, 0), width=6)
    draw.line([(320, y + 20), (320, y + 110)], fill=(190, 0, 0), width=4)
    draw.text((330, y + 30), "P = 12 kN", font=font(26), fill=(190, 0, 0))
    draw.text((160, y + 105), "A", font=font(26), fill=(0, 0, 0))
    draw.text((820, y + 105), "B", font=font(26), fill=(0, 0, 0))
    # 顺时针转 90°：文字变成竖排（这正是"拍歪了"的形态）
    rotated = image.rotate(-90, expand=True)
    rotated.save(os.path.join(HERE, "q5_text_rot90.png"))
    return "q5_text_rot90.png"


def main():
    made = [make_q1(), make_q2(), make_q3(), make_q4(), make_q5()]
    print("已生成：" + "、".join(made))
    return 0


if __name__ == "__main__":
    sys.exit(main())