# Changelog

本文件记录 dsh-mech-workbench 的版本变更。格式遵循 Keep a Changelog，
版本号遵循语义化版本（SemVer）。

## [未发布]

### 新增

- **数学排版（mathtext）写进工具说明**：所有文字（标题、轴名、图例、文字标注、表格）
  都支持 `$...$` 公式，例如 `$\frac{\sin x}{x}$`、`$\sigma_1$`、`$\sum_{n=1}^{\infty}a_nx^n$`；
  中文写在公式外面。引擎一直支持，但工具描述里一个字都没提，模型自然想不到用。
- **`math_fontset`**：`dejavusans`（默认）/ `dejavuserif` / `cm` / `stix` / `stixsans`，
  `cm` 与 `stix` 是论文里常见的衬线数学字形。
- **`font_family` / `font_fallback`**：可覆盖默认字体链；默认链末尾固定带 `DejaVu Sans` 兜底。
- **`latex`（真 LaTeX）**：`true` 需要本机装好 `latex` + `dvipng`，缺就明确报错而不是静默降级；
  `"auto"` 装了就用、没装则回退 mathtext 并在 warnings 里说明；
  `usetex` 排不了中文，所以 spec 里出现中文会被拒绝并提示改用 mathtext。

### 修复

- **缺字形（豆腐块）的真正根因**：原来只设 `rcParams["font.sans-serif"]`，而 `font.family`
  仍是 `["sans-serif"]`；matplotlib ≥3.6 的逐字形回退要求 `font.family` 给出一串具体字族，
  因此 `⇒`(U+21D2)、`σ₁`(U+2081) 这类字符在中文标题里会画成空心方框。
  现在 `font.family` 直接设成具体字体链，缺字逐字形回退到 `DejaVu Sans`，不必再改字。
- **去掉没有粗体的字族**（如 SimHei 与 Microsoft YaHei 同时命中时）：`fontweight="bold"`
  会触发 `findfont: Failed to find font weight bold` 并把字重悄悄退回常规体；现在只保留
  链里能顶上的字族，同时保留「链里只有它一个中文字族」时不动它的保护。
- **Pillow 后端**：数学排版相关设置被忽略时如实说明，不再无声无息。

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