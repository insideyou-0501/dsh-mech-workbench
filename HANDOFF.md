# 交接单：dsh-mech-workbench（通用工科题解工作台）

> **读这一份就够，不必重新探索。** 本文件描述的是**当前**状态。
> 工作区：`C:\Users\丁曹扬\Documents\deepseek-harness\default-workspace\mech-workbench`
> 已安装为 bundle：`dsh-mech-workbench@0.2.0`（Host-only，无 npm 依赖、无构建步骤）。

## 一句话状态

**功能已完成、修改也已装进 live 代次，`python test/run_all.py` 12 项全绿（含"安装产物"验证）。**
**剩下只差一步：重启 DSH**（bundle 变更必须重启才生效）。

> 安装现状（2026-10-20 晚核对，**别照抄旧代次名**）：
> - 当前 live 代次是 `dsh-mech-workbench+0.2.0+4ca2120ccb65`（`profiles/.generations/desired.json` 里那一条），
>   本文档早先写的 `+82f4f841cce2` 已经过期。
> - **工作区里有 4 个文件已改，并且已经装进代次**（`index.js`、`lib/images.js`、
>   `scripts/preprocess.py`、`scripts/vision.py`；`source/` 与 `node_modules/` 两处都已更新，
>   哈希核对一致）。装前的旧文件备份在工作区 `.tmp/installed-backup/`，可回滚。
>   `test/` 不随包发布，不需要也不应该装。
> - **只差重启 DSH**：当前会话的工具集合仍是会话启动时绑定的那一份（旧代码），
>   **一切"现场验证"都必须在重启后重做**。
> - 装的办法见下面「安装 / 更新」一节。**注意 `plugin_manager` 工具只在创造模式的会话里出现**，
>   standard 会话（本文件所在工作区的普通会话）根本没有这个工具——上一轮是靠创造模式装的，
>   本轮是直接复制文件（等效做法，见那一节）。

> 更早修掉的两个真 bug 见下面 🔴 / 🟠 两段；本轮又修掉两个（方向转正、第二读者丢转写），
> 见 🟡 段。

产品定位（改了代码请守住这条）：**不是材料力学专用工具**，而是
「任意学科的题图 → 回读自检 → 求解 → 可视化 → 出图后自检」的通用链路。
学科差异只能表达为**数据**（`scripts/presets.py` 的预设），不能写成学科专用代码路径。

## 🔴 已经修掉的那个线上真 bug（读这段，别重犯）

**症状**：五个回传图片的工具（`mech_read_problem`、`mech_verify_reading`、`mech_diagram`、
`viz_figure`、`viz_annotate`）在真实会话里**全部失败**，模型只看到：

```
tool "mech_diagram" returned invalid output: value is not lossless JSON
```

而 `mech_analyze`（不回传图片）一切正常。**PNG 其实已经写好、说明文字也拼好了**，
但整次调用被判非法，模型连路径都拿不到——外部会话只能靠"猜到产物路径再从磁盘读回来"绕。

**根因**（DSH 源码 `@deepseek-ai/dsh-util-values` 的 `walkJsonValue`，逐条核对过）：

> 工具返回值必须能"无损 JSON 往返"，否则整次调用失败。`undefined` 值的键、`NaN`/`±Infinity`、
> **`-0`**、类实例等非纯对象、稀疏数组、symbol 键、不可枚举键、环 —— 全部判非法。

踩中的是第一条：`saveImageRef` 的**成功路径只返回 `{ image }`，没有 `note` 键**，而调用处写的是

```js
const { image, note } = await saveImageRef(ctx, result.out)
return { ..., image, imageNote: note }   // 成功时 note === undefined → imageNote: undefined
```

**所以只要图片成功存进附件，工具就必然失败**——这正好解释"五个图片工具一起废、`mech_analyze` 正常"。

**修复**：`lib/images.js` 新增 `toLossless()`，在**注册处**统一净化（`index.js` 里把 `defineTool`
包了一层，9 个工具的 `execute` 返回值全部过一遍）。放在注册处而不是每个 `return` 里手写，
是为了**新增工具自动受保护**。同时按官方 `referenceImageValue` 的做法，把
`originalDimensions` 再浅拷一层（防类实例）。
零号原则：DSH 官方做法可查——`referenceAttachments(ctx){ return ctx.get("attachments") }`
证实服务名 `'attachments'` 是对的；`AttachmentId(v){ return v }` 证实引用里是纯字符串。

**为什么原来的自测没抓到**（这条教训比 bug 本身重要）：
自测的 mock ctx **直接调 `execute`**，根本不过 DSH 的校验；mock 附件服务又只返回
`{attachmentId, mediaType, bytes}` 这种"部分字段"，与真实实现（完整引用）不同。
**mock 自测通过 ≠ 真实运行时契约通过。** 现在补了三道：

1. `test/selftest.mjs`：`losslessProblems()` **复刻 `walkJsonValue` 的判定**，
   所有工具的返回值与渲染内容块自动过检（`run()` 里统一做，新增用例自动覆盖）。
2. 同文件里的**对照测试**：断言"未净化的 `{width: undefined}` 确实会被判非法"——
   证明上面那道检查不是空转。
3. `test/verify_installed.mjs`：**直接 import 安装副本**（`test/` 不随包发布，
   所以工作区自测证明不了用户实际加载的那份代码），用两种附件服务形状跑真实工具。
   **这就是"验证产物本身"与"验证源码"的区别。**

## 🟠 第二个真 bug：同一个包里两种算符约定（`^` 与 `**`）

**症状**：`viz_figure` 的 `function` 系列写 `cos(x)^3` 会崩，报
`ufunc 'bitwise_xor' not supported for the input types`。

**根因**：`figure.py` 的表达式求值直接把字符串交给 `eval`，只认 Python 的 `**`；
`^` 在 Python 里是**按位异或**，于是数值数组报出上面那种没人看得懂的原生 numpy 错误。
而**同一个包的 `symbolic.py` 是接受 `^` 的**——一套工具两种约定，模型必然写错。
同时这条错误本身也违反"报错说人话"（直接把 numpy 异常抖出去）。

**修复**（`figure.py::evaluate`）：
- `^` 归一成 `**`（正则 `(?<!\*)\^(?!\*)`，避免动到已有的 `**`）；`^` 在绘图表达式里从来不是异或。
- 分门别类捕获 `NameError`/`SyntaxError`/`TypeError`，输出**可读中文**并**列出可用函数与常数**。

**为什么没被测试发现**：`figure_cases.py` **原来一个 `function`/`parametric` 用例都没有**——
表达式求值器**零覆盖**。已补 4 条：`expr_caret_power`、`expr_parametric_caret`（星形线用 `^`）、
`bad_expr_unknown_name`、`bad_expr_syntax`（28 → **32** 例）。
**教训**：某条代码路径"从没被用例碰过"，就等于它从来没被验证过——光看总用例数会骗人。

## 🟡 第三、第四个真 bug：方向转正失效 + 第二读者把转写丢掉

这两个是同一轮修的，都属于**读图链路**。

### 3. `auto_orient` 纯靠 EXIF → 手机拍歪的图永远转不正，**还报"一切正常"**

**症状**：用户拍歪的题图（文字竖排）走 `mech_read_problem` 之后，`auto_orient` 回一句
"EXIF 方向已是正向，未改动"，图**还是歪的**。上层模型看不出来，只能自己写一遍 PIL 代码硬转，
白花几轮（用户原话："导致你自己写了一遍 pil 代码，这很明显提高了工作量和你的响应时间"）。

**根因**：DSH 的附件归一化会**重新编码并剥掉 EXIF**——实测从附件库拿到的那张真实照片
`getexif()` 条目数 = 0、`Orientation = None`，于是 `ImageOps.exif_transpose` 什么也做不了。
**比"转不正"更糟的是它没说自己在猜**。

**修复**（`scripts/preprocess.py`）：
- EXIF 无信息时改用**像素内容判方向**：缩放灰度图 → 取行/列剖面 → 算"带纹强度"
  （变异系数）→ 比值 ≥1.5 判横排、≤0.67 判竖排，中间判 `unknown`。
- **竖排时转 +90 还是 -90 像素统计量判不出来**（实测两候选只差 9%）→ **不猜**：
  采用固定默认 `rotate +90`，同时**另存一张并排的「方向候选对照图」**（A=+90 已采用、B=-90），
  让主模型一眼确认；这张图由 `index.js` **内联进同一次工具结果**（`extraImage`），
  不必再花一轮去读盘。
- **证据不足必须存疑**：图内没有可用墨量结构（纯白/纯色/已被裁空）时，两个分数都是 0，
  比值没有定义。原先会算成 `inf` 并判成"横排、方向正确"——对空白图下了没有依据的结论。
  现在返回 `unknown` 并说明原因（`ORIENT_NO_INK` + `orient_measured()`）。
- **180° 是这套统计量的严格不变量**：横排页即使整页颠倒也判横排。所以横排那条 note
  **必须显式声明"这不排除整页颠倒"**，并给出 `rotate 180` 的补救写法。

### 4. 第二读者（视觉转写）**明明读到了，却把转写丢掉**

**症状**：`mech_read_problem` 的上层只拿到 `parsed=false` 与空对象，看着像"视觉引擎没读出东西"。
**实际**那份转写是完整可用的，只是没套 JSON 壳。

**根因**（两条叠加）：
1. 活跃链上的 provider 原来 `jsonObject = False`（**不发** `response_format`），模型爱回自然语言，
   而 deepseek 恰好在链尾；
2. `extract_json` 失败后**直接 `continue`**，最后一轮失败就掉进兜底分支，**把原文丢了**。

**修复**（`scripts/vision.py` + `index.js`）：
- provider 一律开 `json_object`（不认的端点仍有 400 自动退回纯提示词约束的保护）。
- 解析失败先在本 provider 上做一次**纯文本**的「整理成 JSON」重试（不带图，很便宜），
  成功则 `parsed=true` + `repaired=true` + `status="ok_after_repair"`。
- 重试也失败：**降级但不是失败** —— `ok:true / parsed:false / degraded:true`，
  原文一字不改放进 `raw`，note 明说"它可能仍是可用的转写，按自然语言用"。
- `index.js` 把链路失败情况汇总成"第二读者链路：provider=status"，并把
  "转写**完全**来自主模型自己读图（没有独立复核）"这句话说清楚。

**为什么原来的测试没抓到**（两条教训）：
- 旧用例只测了"EXIF 方向=6 的图必须建议 `auto_orient`"——**把"EXIF 一定可用"这个坏掉的前提
  当成了测试前提**，所以它对本例完全非判别性。
- `vision.py` 的 provider/JSON 逻辑**一行覆盖都没有**（`viz_cases.py` 测的是 `figure.py`，不是它）。

**新增回归**：
- `test/reading_cases.py` 第 7 节 6 条（横排不转 / 竖排真转正 + 像素级核对 / 对照图不许当切块 /
  带 EXIF 仍走 EXIF / 180° 必须声明 / 空白图必须存疑）。新素材 `test/cases/q5_text_rot90.png`
  = "文字竖排 + 无 EXIF"，正是线上故障的形状，由 `make_cases.py` 生成。
- `test/vision_cases.py`（**新文件**，10 条）：把 `call_provider` / `load_key` /
  `urllib.request.urlopen` 换成本地假件，**不联网、不读真实凭据库**，锁住
  "发不发 `response_format`"、"400 回退"、"整理重试"、"原文绝不丢"、"attempts 如实记录"。
- `test/selftest.mjs` 新增 1 条：竖排图 → 对照图内联成**第二张图**、且**不混进 `pieces`**。

## 九个工具（`index.js`）

| 工具 | 后端脚本 | 学科相关性 |
|---|---|---|
| `mech_read_problem` | `preprocess.py` (+`vision.py`) | 无关 |
| `mech_verify_reading` | `render_math.py` | 无关 |
| `solve_math` | `symbolic.py` | **完全无关** |
| `viz_figure` | `figure.py` | **完全无关** |
| `viz_check` | `vision.py` | **完全无关** |
| `viz_annotate` | `annotate.py` | 无关 |
| `mech_analyze` | `analyze.py` | 力学（静定） |
| `mech_diagram` | `figure.py` + `presets.py` | 力学（只是预设） |
| `mech_card` | `lib/cards.js` | 无关 |

4 个随包技能：`engineering-problem-solving`、`problem-image-reading`、
`engineering-visualization`、`engineering-mechanics`。

## 自测现状（全部通过，最后一次在本轮改动后重跑）

一条命令跑完全部：**`python test/run_all.py` → 12 项全绿**（会自己找无 matplotlib 的解释器；
找不到就**如实列出跳过项**，不假装通过）。

| 测试 | 结果 |
|---|---|
| `node test/selftest.mjs` | **28/28**（含无损 JSON 校验 + 内联路径 + 净化器自身 + 变形口径 + 竖排对照图不当切块） |
| `node test/verify_installed.mjs` | **11/11**（校验**已安装产物**，两种附件服务形状 + 竖排对照图内联） |
| `python test/symbolic_cases.py` | **43/43**（sympy 精确路径）**+ 43/43**（`SYMBOLIC_NO_SYMPY=1` 降级路径） |
| `python test/figure_cases.py` | **32/32**（matplotlib；含 `^` 乘方与表达式报错回归） |
| `python test/figure_cases.py --force-backend pillow` | **32/32**（Pillow，主解释器，走 spec 的 backend 字段） |
| `python test/figure_cases.py`（用无 matplotlib 的解释器） | **32/32** |
| `python test/viz_cases.py` | **10/10**（两个解释器各一遍） |
| `python test/vision_cases.py` | **10/10**（第二读者链路，离线假件） |
| `python test/reading_cases.py` | **66/66**（含第 7 节 6 条方向转正回归） |
| `python test/cases/verify_cases.py <out_dir>` | 缺字形(tofu)检查 **通过**；版式检查因期望名过期**如实报告未覆盖** |

> 测试文件分布（别再说错）：`test/` 下有 `selftest.mjs`、`verify_installed.mjs`、
> `symbolic_cases.py`、`figure_cases.py`、`viz_cases.py`、`reading_cases.py`、
> **`vision_cases.py`**、`run_all.py`；
> `test/cases/` 下有 `verify_cases.py`、`make_cases.py`、`run_inline_specs.py` 和素材/规格
> （素材含 `q1_beam` / `q2_three` / `q3_dark` / `q4_exif_rot` / **`q5_text_rot90`**）。
> **`verify_cases.py` 是存在的**（在 `cases/` 子目录里）——上一轮文档一度写成"不存在、是编的"，
> 那是只 glob 了 `test/*.py`、漏了子目录导致的**错误结论**，已更正。
> 另外 `figure_cases.py --expect-backend pillow` 在**装了 matplotlib 的解释器**上必然失败，
> 因为 `figure.py` 会正确地优先用 matplotlib；要验证 Pillow 路径请用 `--force-backend pillow`
> 或换无 matplotlib 的解释器。

端到端真跑（真题 `test/cases/q1_beam.png`：简支梁、q=5 kN/m 于 0~2 m、P=10 kN 跨中、L=4 m、W=120 cm³、[σ]=160 MPa）：
预处理 ✓ → 视觉转写（真实调用 `deepseek-v4-flash-vision-exp`，2.9 s，`low_confidence: []`，与我肉眼读的题面完全一致）
→ `analyze.py` 给 R_A=12.5 kN / R_B=7.5 kN ✓ → `symbolic.py` 参数式解 `RA=(LP+2Lq-2P-2q)/L` ✓、
σ=M·1000/W=125 MPa（复核通过）✓ → `figure.py` 用 analyze 的 Q/M 数组出双图 ✓。
产物：`test/out/e2e/`。驱动脚本：`.tmp/e2e_run.py`。

## 架构（改代码前必读）

```
figure.py    通用绘图引擎：19 种通用系列类型 + 直角/对数/极坐标/三维 + 多子图 + 图元层
             matplotlib 优先，Pillow 兜底；两条路径的 kind 集合完全一致（不静默丢系列）
   ↑ 只吃"通用系列"
presets.py   学科预设 → 通用系列的**数据化**展开：axial/shear/moment/torque/stress/mohr/deflection
   ↑ 只吃"学科参数"
mech_diagram / viz_figure  →  都经 figure.py
```

`presets.py` 的关键约定：
- 支持 `points`（曲线/折线）与 `segments`（阶梯）两种输入；**剪力图原来只认 segments，
  分布载荷下剪力是线性的，用 points 会被挡住 —— 已修：缺 segments 而有 points 时自动转曲线画法。**
- `main_title`（调用方显式给的标题）**优先于**预设默认标题。
- 端值标注：两端都是 0 时一个都不标（否则就是"同类信息重复标注"）；两端同值只标一次。
- 多图纵向排列时标注走 `_callout_offset` 的"外侧但贴近曲线"分支（既躲开子图标题，也不推进阴影填充区）。
- 下标字符 `σ₁`/`σ₂`（U+2081/2082）在 matplotlib 与 Pillow 两套字体里都渲染成**空心方框**，
  所以一律用 ASCII 的 `σ1`/`σ2`。

`symbolic.py` 的关键约定：
- `--spec` / `--spec-json`，stdout **恰好一个** JSON；成功 `{"ok":true,...}`，失败 `{"ok":false,"error":"中文原因"}` + exit 1。
- 每个 op 都有**独立复核**（残差、求导往返、辛普森/截断对比、特征值残差、R²、密集扫描…），
  不过就 `pass:false` + `warnings`，**不静默给错答案**。
- 缺 sympy 时走 numpy 降级路径，会**声明降级**；涉及符号参数而数值路径做不了的，返回可读的 `unsupported`。
- `evaluate` 的表达式走 AST 白名单（拒绝非数学语法），`_local_dict` 给每个标识符新建 Symbol
  （否则 `I/E/N/Q/S/O` 会被 sympy 命名空间吃掉，产生静默错答案 —— 这是踩过的真 bug）。

通用铁律：
1. 不许有学科专用代码路径（只允许数据化预设）。
2. 不许静默降级（要么等价数值路径 + 声明，要么可读 `unsupported`）。
3. 每个数值结论都要有**独立算法**的复核，不是把同一公式再算一遍。
4. 报错用中文说人话，不把 Python 异常直接甩给模型。
5. **工具返回值必须能无损 JSON 往返**（`undefined` 键 / NaN / ±Infinity / `-0` / 类实例 /
   稀疏数组 / 环 一律非法），否则整次调用被判失败、模型连产物路径都拿不到。
   统一由注册处的 `toLossless` 兜住，新增工具自动受保护——见上面那个 🔴 段。
6. **mock 自测通过 ≠ 真实运行时契约通过**。改了工具返回值形状、附件、或任何与宿主
   约定的东西，必须用 `test/verify_installed.mjs` 在**安装产物**上再验一遍。

## 安装 / 更新（每次改完代码都要做）

```
plugin_manager action=install_bundle target=C:\...\default-workspace\mech-workbench
```

- **这个工具只在创造模式的会话里存在**。standard 会话的工具表里**没有** `plugin_manager`
  （已核对两个会话的 `request/header`：旧会话出现 41 次，standard 会话 0 次）。
  所以"装 bundle"这一步要么切到创造模式，要么由用户自己在那边发一句话让智能体执行。
- **没有 `plugin_manager` 时的等效做法**（本会话实测可用，需要一次 `danger-full-access` 授权）：
  本 bundle 是 Host-only、无 npm 依赖、无构建步骤，所以"安装"= 把文件复制进代次。
  `generation.json` 只记 `pluginName` / `version` / `sourceSpec`（**没有内容哈希**），
  代次目录名里的 `+4ca2120ccb65` 只是个 ID，复制文件不会造成哈希不一致。
  注意 `source/` 与 `node_modules/` 里的同名文件是**硬链接**，直接覆写会断链、
  留下另一份旧内容 —— **两个路径都要复制**：
  ```powershell
  $g = "$env:APPDATA\dsh-desktop\harness\profiles\.generations\live\dsh-mech-workbench+0.2.0+4ca2120ccb65"
  foreach ($f in @("index.js","lib\images.js","scripts\preprocess.py","scripts\vision.py")) {
    Copy-Item "$ws\$f" "$g\source\dsh-mech-workbench\$f" -Force
    Copy-Item "$ws\$f" "$g\node_modules\dsh-mech-workbench\$f" -Force
  }
  ```
  **装之前先把旧文件备份**（本会话备份在 `.tmp/installed-backup/`）。
- 需要**一次授权**（写 profile 目录）。**不要**手改 profile 的 `package.json` / `cordis.patch.yml`。
- 装完 `application: restart-required` —— **bundle 变更必须重启 DSH 才生效**；
  当前会话里的工具集合是会话启动时绑定的，重启前仍跑旧版。
- 安装副本通过 junction 指向 live 代次：
  `profiles/web/node_modules/dsh-mech-workbench` →
  `profiles/.generations/live/<代次>/node_modules/dsh-mech-workbench`（**同一份物理文件**，
  另有一份 pristine 在 `<代次>/source/dsh-mech-workbench`）。
- boot 自带 Node 是 v24.17.0，`node:zlib` 有 `zstdDecompressSync` —— 想读 `session.v4.jsonl.zstd`
  可以用它（但**只解得出第一帧**，多帧要按 `28 b5 2f fd` 魔数切分逐帧解，见 `.tmp/dump_session.mjs`）。
- 已验证安装副本**自包含**：`.pylibs/`（sympy 1.14.0 + mpmath 1.4.1，41 MB）在
  Python 3.13.7 与 3.12.14 两个解释器下都能从包内加载，不依赖全局环境。

## 环境与坑（省得重查）

- 解释器：`D:\游戏\python.exe`（3.13.7，有 matplotlib）、
  `<DSH>\office-runtime\primary-runtime\dependencies\python\python.exe`（3.12.14，**无 matplotlib**）。
  可用 `DSH_MECH_PYTHON` 指定。
- **PowerShell 控制台是 GBK**：脚本必须 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`；
  跑脚本前先 `[Console]::OutputEncoding=[System.Text.Encoding]::UTF8`。
- **不要用 PowerShell 的字符串 `.Replace()` 改源码文件**：`@(@('a','b'))` 会被展平，
  结果逐个单字符替换（本会话真把整个文件改坏过一次）。改文件一律用 `edit` / `write`。
- spec 一律走**临时文件**（`--spec`），不要内联到命令行：Windows 引号 + GBK 会毁掉 UTF-8。
- spec 文件读 `utf-8-sig`（BOM 会让 `json.load` 直接报错）。
- 视觉引擎链：`siliconflow-qwen → zhipu-glm → deepseek → sensenova → gemini → VISION_FALLBACKS`，
  只用已配 key 的；SiliconFlow 账号余额不足（HTTP 402），实际能用的是 `.credentials.yaml` 里的
  `DEEPSEEK_API_KEY`（`deepseek-v4-flash-vision-exp`，付费）。`GLM_API_KEY` 未配。
- `read_image` 能用是因为已在 profile 的 `cordis.patch.yml` 里给
  `aliyun-bailian/deepseek-v4.1-flash` 加了 `input: [text, image]`（工作区外，改需授权）。
- 环境里 **没有** `pip install`（sandbox EPERM）→ 依赖靠 `tools/vendor_pylibs.py` 直接解 wheel 到 `.pylibs`。

## 静不定路线（已实测可行的流程）

**`analyze.py` 不会做静不定**（纯平衡方程求解器），但**力法可以手工建立、交给 `solve_math` 解**，
而且复核链条是完整的。已用一端固定一端铰支梁（L=4 m，q=10 kN/m 全跨，1 次静不定）全程验证：

| 步骤 | 用什么 | 结果 |
|---|---|---|
| 力法方程（选 R_B 为多余约束） | `solve_math` op=solve | `X*L³/(3EI) − qL⁴/(8EI) = 0` → **X = 3Lq/8**（精确，残差 0） |
| 反力回代 | 平衡 | R_A=25 kN，R_B=15 kN，M_A=−20 kN·m |
| 挠曲线（**独立方法**：直接积分 M，而非力法代数） | `solve_math` op=dsolve | `v(x)=−5x⁴/(12EI)+25x³/(6EI)−10x²/(EI)`，初值校核通过 |
| **协调残差** | `solve_math` op=evaluate | **v(4) = 0（精确 0）** ← 多余约束位移为零 |
| 闭式解对照（教科书表） | 手算 | R_A=5qL/8、R_B=3qL/8、M_A=−qL²/8、\|M\|max=9qL²/128@x=5L/8、v_max=0.005416qL⁴/EI —— **全部对上** |
| 挠度极值 | `solve_math` solve+evaluate | x_max=(15−√33)/4=2.313859 m，v_max=−275√33/128−195/128=−13.865271/EI |

**这个流程的可信度来源**：闭式解对照 + 协调残差 + 独立解法（积分 vs 力法代数）。
**它的真实风险**：力法方程是**手工建立**的，写错方程不会有任何工具能发现——
本会话就发生过一次（把力臂写错，内核忠实解了错方程，代回复核照样通过）。
所以：**没有独立参照物（闭式表 / 第二种方法）时，静不定只能给"待核"的结果，不能给结论。**

**要做成自动的（路线 B）需要**：自动判静不定次数 → 选多余约束 → 单位载荷法/图乘法算
δᵢⱼ、ΔᵢP → 解力法方程 → 回代 → 挠曲线；且必须带 4 项复核（平衡、协调残差、
闭式解回归、能量法交叉验证）。刚架/桁架则要上刚度法（路线 C）。

### 第二个实测案例（拉压静不定，含"核对别人的答案"）

教材题 **1-32**：刚性横梁 CD（C 为固定铰），杆 1 竖直在距 C 为 a 处，杆 2 与竖直线成 α
在距 C 为 2a 处，D 在 3a 处受 F 向下；两杆 E、A 相同，竖直高度 l。
（只有 1 个力矩平衡方程、2 个杆力未知 → **1 次静不定**。）

**结论：第三方（另一个 AI）给出的答案是对的**，且我用 4 条独立路径复核：

| 复核 | 方法 | 结果 |
|---|---|---|
| ① 联立（平衡+协调+胡克） | `solve_math` solve | N₁=3F/(4cos³α+1)、N₂=6Fcos²α/(4cos³α+1)，残差 0 |
| ② **协调关系用精确几何验证** | 转动后的**两点距离**做中心差分 | 杆 2 得 1.41421355（=2a·cosα=√2）、杆 1 得 1.00000000（=a）——**"沿杆方向投影"这个偷懒说法被证实一阶成立** |
| ③ **能量法（第二种方法）** | Π(θ)=(EAa²θ²/2l)(1+4cos³α)−3Faθ，dΠ/dθ=0 | θ\*=3Fl/(EAa(1+4cos³α))，回代 N₁=EAaθ/l → 化简得 **3F/(4cos³α+1)**，与①完全一致 |
| ④ 数值收口 | α=45°、F=10 | 平衡残差 **0**；N₁=N₂=12.4264 kN |

**顺手抓到的两个问题**（都很值得记）：
1. **第三方的"α=0、α=90°"两条自查其实是非判别性检查**：若把题面"长度 l"误读成
   "两根杆都长 l"（正确读法是顶棚与横梁水平、竖向间距恒为 l，故杆 2 长 l/cosα），
   则 N₁=3F/(1+4cos²α)、N₂=6Fcosα/(1+4cos²α)——**这个错读在 α=0 和 α=90° 处与正确解完全相同**
   （cos³=cos² 当 cos∈{0,1}），只在中间角度暴露（α=45° 差 24%，差值 = 30√2−40 = 2.4264）。
   所以那两条校验**根本验证不了它对 l 的读法**；判别性证据来自"顶棚与横梁平行 ⇒ 竖向间距恒定"。
   凡是"边界角度自查"，都要问一句：**它能不能区分我实际用的假设？**
2. 第三方完全没提**水平力**：杆 2 的水平分量 N₂sinα = 30−15√2 = 8.787 kN 必须由 C 处
   固定铰承担——若 C 不能承受水平力，题目条件就变了。

**已知工具限制（路线 A 里撞到的）**：`symbolic.py` 的 `limit` **没有参数正负假设机制**，
`sqrt(l²)` 化简不出 `l`，于是几何求极限会返回 `oo*sign(...)` 这种没法用的结果
（不是算错，是缺假设）。**变通办法**：用具体数值 + 中心差分/数值求值（本次就是这么做的）。
要根治得给符号层加 `assumptions`（如 positive=True）。

**变形口径（已修正）**：`analyze.py` **不做任何变形计算**，`section.E` 只被记录、不参与运算。
原先只在"未给 E"时警告"挠度无法计算"，读起来像"给了 E 就能算挠度"——已改成**给不给 E
都明确声明"不计算变形"**，并指向 `solve_math`（有回归断言守住这条口径）。

## 已知边界（诚实清单）

1. `mech_analyze` **只解静定**；静不定返回 `determinate:false`，不猜。通用方程求解请用 `solve_math`。
   静不定**现在可以怎么做**见下面「静不定路线」一节（已验证可行的力法流程）。
2. `analyze.py` 不做塑性、压杆稳定、大变形、剪切修正；截面沿 x 常量（变截面拆多段）。
3. 支座仅固定/铰/辊轴；中间铰需自行拆段。
   > 但**支座模型本身已能表达常见静不定梁**：铰+辊轴=静定；固定+辊轴=1 次；
   > 固定+铰=2 次；固定+固定=3 次。缺的只是求解方法，不是建模能力。
4. **Pillow 兜底后端的视觉质量低于 matplotlib**。刻度已改为 1-2-5 整齐取值
   （`_nice_ticks`），全正弯矩图也**不再跨到负值区**（起点正好在 0），等比例面板
   （莫尔圆）不会被刻度吸附破坏（实测圆外接框宽高比 1.002）。
   残留的只是"留白偏松"这种观感问题（预设自身 padding + Pillow padding 叠加），**不是错值**。
   在装了 matplotlib 的解释器上会优先走 matplotlib，画质更好。
5. `viz_check` 的判断**不能替代力学判断**：本会话实测它把"剪力突变 10 kN 与弯矩峰值对应不上"
   判为 FAIL，那其实是它自己不会解题（图是对的）。视觉评审只用于图面质量（乱码/压图/越界/裁切）。
6. `mech_read_problem` 的 `second_reader:true` 已在端到端跑通（见上）；`viz_annotate` 的
   `text` op 现在也接受把 `x`/`y` 直接写在 op 上（模型很容易这么写，不该因此报错）。
7. `symbolic.py` 约 1500 行，超出当初给自己定的"≤950 行"契约。功能与测试都对，
   但如果要重构，注意 `_check_solution` 必须把 str 键的解映射回**真实 Symbol 对象**（否则带
   `assumptions` 时会假阴性），`op_solve` 里 `sympy is None` 的判断必须在 `_equations` **之前**。
8. `auto_orient` 的像素判方向**只能分"横排 / 竖排"**，三条边界必须心里有数：
   - **180° 是严格不变量**：整页颠倒的横排页仍判横排（靠 note 里"不排除整页颠倒"那句话提醒，
     不会自动纠正）；
   - 竖排时 **+90 还是 -90 判不出来**（两候选统计量只差约 9%）→ 固定默认 +90 **并附对照图**，
     靠人/主模型看那一张图确认；
   - **图内没有可用墨量结构**（空白页、纯色、已被裁空的区域）→ 返回 `unknown`，不硬判。
   还实测过一种**强干扰合成图**（全高暗边 + 大块阴影 + 文字）：列向带纹被暗边抬高，
   比值落在中间 → 如实存疑。**这一种没有被锁进用例**，因为它的判定结果依赖阈值取值、
   用测试固化反而会挡住以后调阈值 —— 记在这里当已知边界，别当成 bug。
   （真实手机照片 `mech-images/q130_raw.jpg` 实测 ratio=0.46 → 正确判竖排并附对照图。）

## 🟥 上一层会话是被 400 打断的（**不是**本插件的 bug，别误判）

2026-10-20 14:53 那轮，上一个会话在**第 11 轮第 44 步**和**第 12 轮第 1 步**连续两次整轮失败：

```
400: {"message":"<400> InternalError.Algo.InvalidParameter: Download multimodal file timed out",
      "type":"invalid_request_error","param":null,"code":"invalid_parameter_error"}
```

- **来源是模型服务端**（当前 provider 是 `aliyun-bailian` / `deepseek-v4.1-flash`，
  `api: openai-completions`），**不是**工作区代码、不是 DSH 校验、不是插件返回值的无损 JSON 问题。
  （无损 JSON 那一类会报 `returned invalid output: value is not lossless JSON`，字面完全不同。）
- "Download multimodal file timed out" = 服务端**取多模态文件（图片）超时**。
  那轮对话里挂着 6 张以上大图（2364×1773 + 4×1080×2374 + 若干截图），而**每一步都会重发全部图片**：
  轮次越长、步骤越多，撞上这个超时的机会越多。紧接着用户执行的 `/compact` 也被 abort。
- 所以它是**传输/服务端的偶发+体量问题**，不是逻辑错。可做的缓解：
  1. **减小图片体量**：附之前先缩到长边约 1600px 存 JPEG（2.5 MP 的手机原图直接塞进去最吃亏）；
  2. **少放图**：同一轮对话里不要反复附同一批大图；已落盘的图让工具**按路径**读；
  3. **别让单轮跑太长**（步骤多 = 图片被重发的次数多）；
  4. 撞上就直接重试一次——它常常是瞬时的。
- 顺带记一个**本插件自己的体量取舍**：竖排图那条路现在**一次返回两张图**
  （主图约 50 KB + 方向候选对照图约 194 KB @1872×750）。多这 194 KB 是为了**省掉一整轮**
  （否则要再调一次工具/再读盘才能确认方向），净账是划算的；若哪天嫌大，
  调 `preprocess.py::_orient_sheet` 的 `target_height`（现值 700）即可。

## 可选收尾

- [x] `scripts/diagram.py`（旧版专用绘图，30 KB）**已删除**：它曾是材料力学专用渲染路径，
      留着与"通用工作台"的定位直接矛盾，且已确认无任何代码引用（只有文档提到）。
- [x] `明日开工.md`（内容过期）**已删除**。
- [x] 无损 JSON 那一类失败**已修并加了三道回归**（见上面 🔴 段）。
- [x] 方向转正失效（`auto_orient` 纯 EXIF）+ 第二读者丢转写**已修**，各带回归（见上面 🟡 段）。
      **并且已经装进代次**（`source/` + `node_modules/` 两处，已备份可回滚）。
      剩下**只差重启 DSH**；重启后请现场复验：拿一张**拍歪的手机照片**跑
      `mech_read_problem`，应当（a）判出竖排并转正、（b）返回两张图（主图 + 方向候选对照图）、
      （c）`pieces` 里没有那张对照图。
- [ ] `test/cases/verify_cases.py` 的**版式检查期望名已过期**（还用 `p1/p3/p4 + pieces`，
      现在素材是 `q1_beam/q2_three/q3_dark/q4_exif_rot`）。现在会如实报告"未覆盖"，
      但要真正恢复版式检查，得把期望名对齐到当前用例集的产物名。
- [ ] Pillow 后端"留白偏松"的观感（见"已知边界"第 4 条）——纯观感，不影响数值。
- [ ] `index.js` 里 `mech_read_problem` 的 JSON schema 还没把 `preprocess.py` 新增的
      17 种算子与 16 个 probe 字段写进描述。