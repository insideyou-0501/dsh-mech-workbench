# Changelog

本文件记录 dsh-mech-workbench 的版本变更。格式遵循 Keep a Changelog，
版本号遵循语义化版本（SemVer）。

## [未发布]

### 新增

- **图例排版可配置**（`viz_figure` / `mech_diagram` 都走同一套 `figure.py`）：
  面板支持 `legend_ncol`（列数）、`legend_size`（字号）、`legend_loc`、`legend_framealpha`，
  也支持对象写法 `legend: {"ncol": 2, "size": 9, "loc": "upper left"}`。
  原来字号写死 8、列数写死 1，六个系列就排成竖着一长条盖住曲线，
  而且这些字段传进来会被**静默忽略**（不报错、也不生效）。
- **`inset` 局部放大窗系列**：`{"kind": "inset", "box": [x0,x1,y0,y1], "at": [l,b,w,h], "series": [...]}`。
  父图用虚线框标出放大的区域，另开一小块坐标系画同源系列；
  原来只能靠 `grid 1x2` 拼两个不同 `xlim` 的子图，代价是整个版式被占掉一半。
- **Pillow 后端有图例了**：原来一条都不画，多系列图只能靠猜颜色，`unsupported` 里也不提。
  现在按 `legend_ncol` 排两列，位置由 `legend_loc` 决定（九宫格映射）。
- **数学排版写进工具说明**：`viz_figure` 的描述里说明 `$...$` mathtext 可用
  （`\frac \sqrt \sum \int \alpha \sigma_1` 等），不再让调用方以为只能画纯文本。

### 修复

- **缺字形不再静默**：字体里没有的字（`⇒`、`σ₁` 用到的 U+2081）会被画成空心方框，
  而返回值的 `warnings` 是空的——matplotlib 只把这件事写进 stderr。现在两个后端都会在
  `warnings` 里报「字体缺字形：₁U+2081 …」，并提示改用 ASCII 或 mathtext。
- **`style` 接受对象写法**：`{"style": {"linestyle": "--", "color": "#c62828"}}` 原来直接崩
  `ValueError: Unrecognized linestyle: {...}`；字符串写法行为不变。
- **`note` 上限两后端统一为 4 条**：原来 matplotlib 4、Pillow 3，同一份 spec 换后端会少一条说明；
  超出的部分现在会在 `warnings` 里如实说明。
- **脚注不再压刻度标签**：`note` 占的高度会从绘图区里让出来（约束布局的 `rect`），
  原来它钉在 `y=0.012`，最后一两行会和 x 轴刻度标签叠在一起。
- **矢量输出不再白写**：`out` 写成 `.svg` / `.pdf` 时文件已经生成，却因为用 PIL 回读尺寸
  抛 `UnidentifiedImageError` 判整次调用失败；现在只对位图回读像素尺寸。
- **Pillow 后端写矢量格式给出人话错误**：原来抛 `ValueError: unknown file extension: .svg`，
  现在是「Pillow 后端只能输出位图…矢量输出需要 matplotlib」。

## [0.2.1] - 2026-10-23

### 修复

- **Python 解释器改为从进程位置推断**，不再写死某台机器上的安装路径。
  桌面版的 `<安装目录>/DSH Desktop.exe` 与 `<安装目录>/resources/` 结构固定，
  因此换盘符、换安装目录、或他人安装到默认位置都能自动找到随 DSH 附带的解释器；
  找不到时仍按环境变量、系统 `py` / `python3` / `python` 依次降级。
- 这一项决定插件能否在别人机器上直接跑起来，是上架插件市场前的必要修复。

### 新增

- `assets/icon.png`（512×512）与 `assets/icon-180.png`（180×180）：
  由 `icon.svg` 还原的位图图标，供插件市场卡片使用（市场只接受位图）。

## [0.2.0] - 2026-10-21

### 初始开源版本

- 9 个工具：
  - `mech_read_problem`（题图预处理 17 种算子 + 可选独立视觉二次转写）
  - `mech_verify_reading`（转写渲染成图与原图并排，做读题自检）
  - `mech_analyze`（静定反力、N/Q/M/T、截面几何、应力、8 项独立校核）
  - `mech_diagram`（力学标准图，`panels` 多图共享 x 轴）
  - `solve_math`（通用求解：solve/evaluate/diff/integrate/dsolve/matrix/optimize/fit/table…）
  - `viz_figure`（通用出图引擎，19 种系列 + 直角/对数/极坐标/三维 + 多子图）
  - `viz_check`（独立视觉评审：乱码/压图/越界/裁切/自洽性）
  - `viz_annotate`（原图讲解式标注）
  - `mech_card`（题目卡片 save/get/list/append）
- 4 个随包技能：`engineering-problem-solving`、`problem-image-reading`、
  `engineering-visualization`、`engineering-mechanics`
- 随包 Python 依赖：sympy 1.14.0 + mpmath 1.4.1（`.pylibs`，无 npm 依赖、无构建步骤）
- 安装方式：DSH 创造模式会话内 `plugin_manager` `action: install_bundle`，指向本包目录