# dsh-mech-workbench

**通用工科题解工作台**（DeepSeek Harness 插件）。

定位不是"材料力学题专用工具"，而是把「读题图 → 回读自检 → 求解 → 可视化 → 出图后自检」
这条链路变成模型手里的**确定性工具**，学科由**数据驱动**而不是写死在代码里：

- **读**任意学科的题目照片 / 图表 / 表格（力学、结构、电工、数学、物理、流程图…）；
- **解**任意学科的方程与代数问题（符号精确解优先，缺 sympy 时数值降级**并如实声明**）；
- **画**任意学科的图（一个通用引擎 + 19 种通用系列类型），力学那几张标准图只是
  `scripts/presets.py` 里的**数据化预设**，不是独立代码路径；
- **验**每一步都有独立复核，复核不过就 `pass:false` 并给出 warning，绝不静默给错答案。

## 装

这是一个 **Host-only bundle**（无 npm 依赖、无构建步骤），按 DSH 官方规范用 `plugin_manager` 安装：

1. 在 Web 里新开一个**创造模式**任务（设置 → Agent 预设 → 创造入口，即 `cordis` 预设）；
   只有该预设启用 `@deepseek-ai/dsh-plugin-manager/tools` 与 `@deepseek-ai/dsh-tool-cordis`。
2. 让会话执行：`plugin_manager` `action: install_bundle`，`target` 指向本包目录的绝对路径。
   安装需要一次授权；**不要**手改 profile 的 `package.json` / `cordis.patch.yml`，那是 `install_bundle` 的职责。
3. 用 `cordis_inspect_query` 确认插件行已注册，再按需新开会话使工具生效（bundle 变更需重启 DSH）。

## 九个工具

| 工具 | 作用 | 关键输入 |
|---|---|---|
| `mech_read_problem` | 题目照片预处理（转正/去斜/透视/二值化/一页多题分栏…共 17 种算子）+ 可选独立视觉模型第二次转写 | `image`、`ops`、`split`、`second_reader` |
| `mech_verify_reading` | 把转写渲染成图与原图并排输出，做**读题自检**（发图前必做） | `original`、`transcription` |
| `mech_analyze` | 静定反力、N/Q/M/T 分布与极值、截面几何、应力、8 项独立校核 | `spec`（见下） |
| `mech_diagram` | 力学标准图（轴力/剪力/弯矩/扭矩/应力分布/莫尔圆/挠曲线），`panels` 多图共享 x 轴 | `spec` |
| `solve_math` | **通用**求解内核：solve/evaluate/diff/integrate/limit/series/dsolve/matrix/optimize/fit/table/expand/factor/simplify… | `spec.op` |
| `viz_figure` | **通用**出图引擎（学科无关）：19 种系列类型 + 直角/对数/极坐标/三维 + 多子图 + 图元层 | `spec` |
| `viz_check` | 独立视觉评审：文字乱码/压图/越界/裁切、图形自洽性，给出 PASS/FAIL | `image` |
| `viz_annotate` | 在**学生原图**上做讲解式标注：框/圈/箭头/尺寸线/文字/局部放大/坐标网格 | `src`、`ops` |
| `mech_card` | 题目卡片 `save/get/list/append`，落到 `<workspace>/.mech-cards/` | `action`、`card`/`id`/`field` |

配套随包技能 4 个：`engineering-problem-solving`（总控流程与硬关卡）、`problem-image-reading`（读图规范）、
`engineering-visualization`（可视化规范）、`engineering-mechanics`（力学符号约定与常见陷阱）。

## 通用性原则（改代码前先看这条）

1. **不许有"某某学科专用代码路径"**。学科差异只能表达为 `scripts/presets.py` 里的预设数据。
   力学图 = 通用引擎 + 预设展开，`figure.py` 不认识"弯矩"这个概念。
2. **不许静默降级**。缺 sympy / 缺 matplotlib / 缺视觉 key 时，要么走数值等价路径并声明，
   要么返回可读的 `unsupported`，绝不给一个看起来正常的错答案。
3. **每个数值结论都要有独立复核**。复核是独立算法（残差、求导往返、辛普森对比、特征值残差、
   密集扫描、截断复核…），不是把同一个公式再算一遍。
4. **报错用中文说人话**，不把 Python 异常直接甩给模型。
5. **工具返回值必须能过 DSH 的"无损 JSON"校验**：`undefined` 值的键、`NaN`/`±Infinity`、`-0`、
   类实例等非纯对象、稀疏数组、环——任意一条都会让**整次调用失败**，模型连产物路径都拿不到
   （踩过：`imageNote: undefined` 让五个回传图片的工具全线失败）。统一由注册处的 `toLossless` 兜住。
6. **mock 自测通过 ≠ 真实运行时契约通过**。改了工具返回值形状或与宿主的约定，
   必须用 `test/verify_installed.mjs` 在**已安装产物**上再验一遍。

## 符号约定（工具与技能一致）

- 轴力 **N 拉为正**（`N = -Σ Fx(左段)`）；剪力 **Q 使微段顺时针转为正**（`+Σ Fy(左段)`）；
  弯矩 **M 下侧受拉为正**；扭矩 **T 右手螺旋 +x 为正**。
- 横向力向上为正、轴向力 +x 为正、分布集度向上为正、集中力偶逆时针为正。
- 默认单位 `m / kN / kN·m / MPa`。

> 注意：`solve_math` / `viz_figure` / `viz_annotate` / `viz_check` 是**学科无关**的，
> 上面这套符号约定只适用于 `mech_analyze` / `mech_diagram`。

## 环境要求

- **Python + numpy + Pillow**：优先用随 DSH Desktop 的 Office 运行时（自带 numpy 2.3.5、Pillow 12.3.0），
  也可用 `DSH_MECH_PYTHON` 指定解释器。除 `figure.py` 外都不依赖 matplotlib
  （`analyze.py`、`annotate.py`、`preprocess.py`、`render_math.py` 都是纯 Pillow/numpy）。
- **sympy（可选，随包自带）**：`.pylibs/` 内含 sympy 1.14.0 + mpmath 1.4.1（约 41 MB），
  两个解释器实测都能从包内加载，**不依赖全局环境**。想验证降级路径：设 `SYMBOLIC_NO_SYMPY=1`。
- **视觉引擎**（`mech_read_problem` 的第二次转写、`viz_check`、`render_math.py` 的公式排版需要）：
  按 `siliconflow-qwen → zhipu-glm → deepseek → sensenova → gemini → VISION_FALLBACKS` 依次尝试，
  只用已配 key 的引擎；key 从环境变量、`~/.dsh/secrets/media-tools.env`、`~/.dsh/.credentials.yaml` 读取，
  **从不写入任何产物**。未配任何 key 时该能力如实报失败，其余工具照常可用。

## 自测（离线，不联网）

```bash
python mech-workbench/test/run_all.py            # ← 一条命令跑完全部 11 项并汇总
```

单独跑：

```bash
node mech-workbench/test/selftest.mjs            # 插件契约 + 无损 JSON + 图片内联 + 净化器（25 项）
node mech-workbench/test/verify_installed.mjs    # 校验**已安装产物**（10 项；不传参数自动找 live 代次）
python mech-workbench/test/symbolic_cases.py                 # 求解内核（43 例，精确路径）
SYMBOLIC_NO_SYMPY=1 python mech-workbench/test/symbolic_cases.py   # 同一批用例跑降级路径（43 例）
python mech-workbench/test/figure_cases.py                   # 出图引擎（28 例，matplotlib）
python mech-workbench/test/figure_cases.py --force-backend pillow  # 同一批用例跑 Pillow 后端（28 例）
python mech-workbench/test/viz_cases.py                      # 图元/标注（两个解释器各 10 例）
python mech-workbench/test/reading_cases.py                  # 预处理与分栏（60 例）
python mech-workbench/test/cases/verify_cases.py <out_dir>    # 渲染产物程序化自检（缺字形/版式/亮度）
```

当前状态：**25/25、10/10、43/43 + 43/43、28/28 × 3（matplotlib / Pillow / 纯 Pillow 解释器）、
10/10 × 2、60/60**，`run_all.py` **11 项全绿**。

> 为什么有 `verify_installed.mjs`：包里的 `test/` **不随包发布**，所以工作区自测只能证明源码对，
> 证明不了"DSH 实际加载的那份代码"对。它会直接 import 安装副本、用两种附件服务形状跑真实工具，
> 并按 DSH 的 `walkJsonValue` 规则检查返回值。**这个文件的存在本身就是一个教训**：
> 曾经五个回传图片的工具因为返回值里带了一个 `undefined` 键（`imageNote`）而全线失败，
> 而 mock 自测完全测不出来——详见 HANDOFF。

> `run_all.py` 需要一个**没有 matplotlib** 的解释器来验 Pillow 降级路径，默认找 DSH 自带的
> Office 运行时；找不到就**如实列出跳过项**（不算通过），可用 `MECH_NOMPL_PYTHON` 指定。

## 目录

```
index.js            插件入口：9 个工具 + 技能提供方
lib/python.js       解释器解析 + 脚本调用（spec 走临时文件；stdout 走文件并强制 UTF-8）
lib/images.js       attachments.saveImage → {type:'image'} 内容块；toLossless() 保证返回值能过 DSH 无损 JSON 校验
lib/cards.js        题目卡片读写
scripts/symbolic.py 通用求解内核（符号优先 + 数值降级 + 每步独立复核）
scripts/figure.py   通用绘图引擎（19 种系列类型，matplotlib 优先、Pillow 兜底）
scripts/presets.py  学科预设 → 通用系列的**数据化**展开（力学标准图在这里，不在 figure.py 里）
scripts/analyze.py  静定反力/内力/截面/应力 + 校核
scripts/annotate.py 原图讲解式标注（框/圈/箭头/尺寸线/放大/网格）
scripts/preprocess.py  照片预处理（17 种算子）与一页多题分栏
scripts/vision.py   视觉引擎故障转移链（含 --doctor 自检）
scripts/render_math.py 转写回渲染对照图
skills/*/SKILL.md   4 个随包技能
.pylibs/            随包 sympy + mpmath（自包含，保证可移植）
```

## 已知边界

1. **`mech_analyze` 只解静定问题**。静不定返回 `determinate: false` 并说明需变形协调/刚度法，不猜结果。
   （通用方程求解请用 `solve_math`，它不受此限制。）
2. `mech_analyze` 不做塑性、压杆稳定、大变形、剪切修正；截面沿 x 常量（变截面需拆成多段 spec）。
3. 支座仅固定/铰/辊轴三种；中间铰需自行拆段建模。
4. `analyze.py` 的 `section.E` 可选：不给就只跳过挠度/变形（会在 `warnings` 说明）。
5. 非圆截面不提供 `Ip/Wp`；管截面 τmax 用 `τ = Q·Sy/(Iy·t)` 近似。
6. `figure.py` 的 Pillow 兜底后端**视觉质量低于 matplotlib**（留白偏松；刻度已改为整齐取值、
   全正弯矩图不再跨负值区）；装了 matplotlib 就优先走 matplotlib，两条路径都不静默丢系列类型。
7. `test/cases/verify_cases.py` 的**版式检查期望名已过期**（沿用旧用例名 `p1/p3/p4 + pieces`），
   当前会如实报告"未覆盖任何文件"；缺字形(tofu)检查仍然有效。

更详细的交接与验证记录见 [HANDOFF.md](HANDOFF.md)。