# Changelog

本文件记录 dsh-mech-workbench 的版本变更。格式遵循 Keep a Changelog，
版本号遵循语义化版本（SemVer）。

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