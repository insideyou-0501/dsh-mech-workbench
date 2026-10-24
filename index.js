/**
 * dsh-mech-workbench —— 工科题解工作台（DeepSeek Harness 插件）。
 *
 * 定位：**学科无关的工科题解工具链**，不按学科枚举图型或题型。
 * 一条流水线：读图转写 → 回读自检 → 求解 → 可视化 → 独立视觉校核 → 存档。
 *
 * 工具：
 *   * `mech_read_problem`   拍照→预处理（转正/增强/放大/分栏）→（可选）独立第二读者转写；
 *   * `mech_verify_reading` 把转写渲染回图、与原图并排，做读题自检；
 *   * `viz_annotate`        在**学生原图**上标注讲解（网格定位、圈重点、箭头、尺寸线、局部放大）；
 *   * `solve_math`          通用符号/数值求解内核（代数、微积分、ODE、线性代数、拟合…）+ 独立验算；
 *   * `mech_analyze`        结构静力内核：反力、N/Q/M/T、截面几何、应力与 8 项不变量校核；
 *   * `mech_diagram`        工程图（力学预设：轴力/剪力/弯矩/扭矩/应力分布/莫尔圆/挠曲线）；
 *   * `viz_figure`          **通用出图引擎**：学科无关的声明式 spec → PNG（学科外观走 preset，不写学科分支）；
 *   * `viz_check`           独立视觉评审（图面质检，也能当"代看"通道）；
 *   * `mech_card`           题目卡片持久化，供后续轮次按 id 取回原图与结论。
 *
 * 以及四个随包技能（见 `skills/`）。
 *
 * 设计约束：工具只做「确定性的事」，物理/数学判断留给模型；任何一步失败都返回可读原因，
 * 不静默失败，也不编造数值。
 *
 * @module dsh-mech-workbench
 */

import { existsSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { appendCard, getCard, listCards, saveCard } from './lib/cards.js'
import { contentFrom, saveImageRef, toLossless } from './lib/images.js'
import { resolvePython, runScript } from './lib/python.js'

const PROVIDER_NAME = 'dsh-mech-workbench'
const SKILL_NAMES = [
  'engineering-problem-solving',
  'problem-image-reading',
  'engineering-visualization',
  'engineering-mechanics',
]

/**
 * 宿主包只在真实 Harness 进程里可解析（Desktop 通过 host module fallback 提供）。
 * 在独立自测中退化为恒等函数：定义形状保持一致，参数校验交给脚本。
 */
let rawDefineTool = (options) => options
try {
  const mod = await import('@deepseek-ai/dsh-tools')
  if (typeof mod.defineTool === 'function') rawDefineTool = mod.defineTool
} catch {
  rawDefineTool = (options) => options
}

/**
 * 所有工具统一在注册处过一道「无损 JSON」净化。
 *
 * DSH 用 `walkJsonValue` 校验工具返回值：`undefined` 值的键、NaN/±Infinity/-0、
 * 类实例、稀疏数组一律判非法，整次调用直接失败。真实踩过的坑是
 * **附件服务不可用时 `{ image: undefined }`**——它让 mech_diagram / mech_read_problem /
 * viz_annotate 全部报 `value is not lossless JSON`，而 PNG 其实早就写好、
 * 说明文字也拼好了，模型却只看到一个序列化错误。
 *
 * 放在这里而不是每个 `return` 里手写，是为了新增工具自动受保护。
 */
function defineTool(definition) {
  const execute = definition?.execute
  if (typeof execute !== 'function') return rawDefineTool(definition)
  return rawDefineTool({
    ...definition,
    execute: async (...args) => toLossless(await execute(...args)),
  })
}

/** 当前时间戳，用于默认文件名。 */
function stamp() {
  return new Date().toISOString().replace(/[-:]/g, '').replace(/\..*$/, '')
}

function workspaceOf(args) {
  return resolve(args?.workspace ?? process.cwd())
}

/** 把极值对象拼成一句人能读的话。 */
function extremaText(extrema, unit) {
  if (!extrema) return ''
  const parts = []
  for (const [kind, value] of Object.entries(extrema)) {
    if (!value) continue
    const suffix = unit ? ` ${unit}` : ''
    parts.push(
      `${kind}: 最大 ${value.max}${suffix} @x=${value.max_x}，最小 ${value.min}${suffix} @x=${value.min_x}`,
    )
  }
  return parts.join('；')
}

function basenameNoExt(path) {
  const name = path.replace(/\\/g, '/').split('/').pop() ?? 'image'
  return name.replace(/\.[^.]+$/, '')
}

/**
 * 把"一步"渲染成人能读的样子。
 *
 * `steps` 里既可能是字符串，也可能是对象（probe 会回 `{sympy, numpy, python, ...}`），
 * 直接 `.join()` 会打印成 `[object Object]`——真实踩到过（solve_math probe 的"过程"一行）。
 * 对象按 `键=值` 展开，并丢掉 null / 空值，避免刷出一堆无意义的字段。
 */
function describeStep(step) {
  if (step === null || step === undefined) return ''
  if (typeof step === 'string') return step
  if (typeof step === 'number' || typeof step === 'boolean') return String(step)
  if (Array.isArray(step)) return step.map(describeStep).filter(Boolean).join('、')
  if (typeof step === 'object') {
    const pairs = Object.entries(step)
      .filter(([, value]) => value !== null && value !== undefined && value !== '')
      .map(([key, value]) => `${key}=${typeof value === 'object' ? JSON.stringify(value) : String(value)}`)
    return pairs.length ? pairs.join('，') : ''
  }
  return String(step)
}

// --------------------------------------------------------------------------
// 工程图：把旧的 `kind` 写法翻成新的 `preset`（同一套预设，老 spec 继续可用）
// --------------------------------------------------------------------------
const DIAGRAM_PRESETS = ['axial', 'shear', 'moment', 'torque', 'stress', 'mohr', 'deflection']

/** 递归把 spec / panels 里的 `kind` 翻成 `preset`，并校验预设名。 */
function translateDiagramKeys(spec) {
  const walk = (node) => {
    if (!node || typeof node !== 'object') return
    if (typeof node.kind === 'string' && DIAGRAM_PRESETS.includes(node.kind) && !node.preset) {
      node.preset = node.kind
      delete node.kind
    }
    if (node.preset && !DIAGRAM_PRESETS.includes(node.preset)) {
      throw new Error(
        `未知的工程图 preset：${node.preset}（可用 ${DIAGRAM_PRESETS.join(' / ')}）；` +
          '其他学科的图型请直接用 viz_figure。',
      )
    }
    if (Array.isArray(node.panels)) node.panels.forEach(walk)
  }
  walk(spec)
  return spec
}

/** 回传本次用到哪些预设（`panels` 按顺序）。 */
function diagramKinds(spec) {
  if (Array.isArray(spec.panels) && spec.panels.length) {
    return spec.panels.map((panel) => panel?.preset).filter(Boolean)
  }
  return spec.preset ? [spec.preset] : []
}

/**
 * 从**调用方给出的数据**汇总极值（不做力学计算，只描述图上画了什么）。
 * 真正的反力/内力极值请用 mech_analyze 算，那里的结果带独立校核。
 */
function diagramExtrema(spec) {
  const out = {}
  const panels = Array.isArray(spec.panels) && spec.panels.length ? spec.panels : [spec]
  for (const panel of panels) {
    const name = panel?.preset
    if (!name) continue
    const samples = []
    for (const segment of panel.segments ?? []) {
      const x0 = Number(segment.x0 ?? 0)
      const x1 = Number(segment.x1 ?? x0)
      samples.push({ x: (x0 + x1) / 2, value: Number(segment.value ?? 0) })
    }
    for (const point of panel.points ?? []) {
      samples.push({ x: Number(point.x ?? 0), value: Number(point.value ?? 0) })
    }
    for (const mark of panel.marks ?? []) {
      samples.push({ x: Number(mark.x ?? 0), value: Number(mark.value ?? 0) })
    }
    if (!samples.length) continue
    let max = samples[0]
    let min = samples[0]
    for (const sample of samples) {
      if (sample.value > max.value) max = sample
      if (sample.value < min.value) min = sample
    }
    out[name] = { max: max.value, max_x: max.x, min: min.value, min_x: min.x }
  }
  return Object.keys(out).length ? out : null
}

// --------------------------------------------------------------------------
// 技能提供方：元数据直接读 SKILL.md 的 frontmatter，避免两处描述不一致
// --------------------------------------------------------------------------
function skillPath(name, ...parts) {
  return fileURLToPath(new URL(`./skills/${name}/${parts.join('/')}`, import.meta.url))
}

function frontmatter(raw) {
  const match = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(raw)
  if (!match) throw new Error('skill 缺少 YAML frontmatter')
  const text = match[1]
  const name = /^name:\s*(.+?)\s*$/m.exec(text)?.[1]
  const quoted = /^description:\s*"([^"]*)"\s*$/m.exec(text)?.[1]
  const plain = /^description:\s*(?!["'])(.+?)\s*$/m.exec(text)?.[1]
  const description = quoted ?? plain
  if (!name || !description) throw new Error('skill frontmatter 需要 name 与单行 description')
  return { name, description, bodyStart: match[0].length }
}

function createSkillProvider() {
  const candidates = []
  for (const name of SKILL_NAMES) {
    const directory = skillPath(name)
    const body = join(directory, 'SKILL.md')
    // 技能是可选取用资源：文件缺失只跳过它，绝不让它拖垮工具注册。
    if (!existsSync(body)) continue
    const metadata = frontmatter(readFileSync(body, 'utf8'))
    if (metadata.name !== name) throw new Error(`${body} 声明的是 ${metadata.name}，期望 ${name}`)
    candidates.push({
      path: body,
      name,
      description: metadata.description,
      invocation: { modelInvocable: true, userInvocable: true },
      provider: PROVIDER_NAME,
      source: 'bundled',
      resourceBase: { kind: 'directory', path: directory },
      rank: 600,
      locator: { body, bodyStart: metadata.bodyStart },
    })
  }
  return {
    name: PROVIDER_NAME,
    list: () => Promise.resolve(candidates),
    async get(candidate, options = {}) {
      const selected = candidates.find(
        (item) => item.name === candidate?.name && item.provider === candidate?.provider,
      )
      if (!selected) return undefined
      const { rank: _rank, locator, ...summary } = selected
      void options
      return { ...summary, content: readFileSync(locator.body, 'utf8').slice(locator.bodyStart).trim() }
    },
  }
}

// --------------------------------------------------------------------------
// 工具定义
// --------------------------------------------------------------------------
/**
 * 图面质检清单。要求视觉模型只输出 JSON，便于直接把结论吃进结构化字段。
 * 我（agent）当前路由不声明图片输入，自己看不见图，所以这张清单就是"代看"的标准。
 */
const QA_PROMPT = [
  '这是一张程序自动生成的图。请只做视觉质检，不要复述题目、不要臆测看不到的内容。逐项检查：',
  '1) 文字与符号是否完整（有无方框、乱码、缺字、被裁切）；',
  '2) 是否出现文字压住图形元素、标注互相重叠、文字越过画布边界；',
  '3) 图形是否自洽（轴名与单位、刻度与数据、图例与曲线、标注数值与图形是否一致，有无明显画错或画反）；',
  '4) 最影响可读性的一处问题是什么。',
  '只输出一个 JSON 对象，不要输出任何其他文字：',
  '{"verdict":"PASS 或 FAIL","text_ok":true,"overlap":["..."],"clipping":["..."],"consistency":["..."],"worst":"最影响可读性的一处问题","detail":"整体说明"}',
  '其中数组字段无问题时给空数组。',
].join('\n')

/**
 * 交给独立视觉模型评审一张图。
 *
 * @param path - 图片路径。
 * @param options - `prompt` 自定义提问（需自行要求 JSON）、`provider` 指定引擎。
 * @returns 结构化结论：`verdict`、`text`、`evidence`、引擎与尝试记录。
 * @throws 所有引擎都失败时抛出（由调用方决定是降级还是上报）。
 */
async function visionReview(path, options = {}) {
  const spec = { src: [resolve(path)], prompt: options.prompt ?? QA_PROMPT }
  if (options.provider) spec.provider = options.provider
  const result = await runScript('vision.py', spec, { timeoutMs: 300000 })
  const parsed =
    result.parsed && result.transcription && typeof result.transcription === 'object'
      ? result.transcription
      : null
  const text = parsed ? JSON.stringify(parsed, null, 2) : String(result.raw ?? '')
  const verdict = parsed?.verdict
    ? String(parsed.verdict).toUpperCase()
    : /\bFAIL\b/i.test(text)
      ? 'FAIL'
      : /\bPASS\b/i.test(text)
        ? 'PASS'
        : 'UNKNOWN'
  return {
    provider: result.provider ?? null,
    model: result.model ?? null,
    parsed: Boolean(parsed),
    verdict,
    text,
    evidence: parsed,
    raw: result.raw ?? null,
    attempts: result.attempts ?? [],
  }
}

function registerTools(ctx) {
  // 1) 读题：预处理 + 可选的独立第二读者转写
  ctx.tools.register(defineTool({
    name: 'mech_read_problem',
    description:
      '读一张题目图片（**任意学科**：力学/数学/电路/热力学/化学/统计图表/表格/多栏试卷，手写或印刷）：' +
      '先做预处理（自动转正、去倾斜、增强、放大、一页多题分栏），再可选调用**独立视觉模型**做第二次转写，' +
      '返回结构化题面（学科、题面 LaTeX、已知、所求、图中要素、表格、公式、低置信字段、看不清的地方）。' +
      '学生发来图片后应优先调用本工具，而不是直接凭记忆读题；数值看不清时用 ops 里的 zoom 放大再看，' +
      '读法有疑问时再用 mech_verify_reading 出对照图核对。',
    parameters: {
      image: { type: 'string', required: true, description: '题目图片的绝对路径' },
      workspace: { type: 'string', description: '工作区根目录；缺省用进程当前目录' },
      out_dir: { type: 'string', description: '预处理结果目录；缺省 <workspace>/mech-images' },
      ops: {
        type: 'json',
        description:
          '预处理操作数组，如 [{"op":"auto_orient"},{"op":"autocontrast","cutoff":1.0},{"op":"upscale","factor":1.6}]；' +
          '缺省 auto_orient + autocontrast。字太小时加 {"op":"zoom","box":[l,t,r,b],"factor":3}（box 用 0–1 归一化）；' +
          '纸张拍歪了用 {"op":"deskew"} 或 {"op":"perspective","corners":[[x,y]×4]}；' +
          '看不清的浅色扫描件用 {"op":"enhance_text"} 或 {"op":"binarize"}。',
      },
      split: { type: 'json', description: '一页多题时分栏：{"count":3,"axis":"y","overlap":0.05}，或 {"mode":"auto"} 按空白自动切分' },
      second_reader: { type: 'boolean', description: '是否调用独立视觉模型做第二次转写，默认 true（需要已配视觉引擎 key；失败会降级为仅由我自己读图，如实说明）' },
      prompt: { type: 'string', description: '自定义转写提示词（覆盖默认的结构化转写提示）' },
    },
    timeoutMs: 300000,
    output: { schema: { type: 'json' }, render: (_args, value) => contentFrom(value, value.summary) },
    async execute(args, exec) {
      const workspace = workspaceOf(args)
      const outDir = args.out_dir ? resolve(args.out_dir) : join(workspace, 'mech-images')
      const base = basenameNoExt(args.image)
      const processed = join(outDir, `${base}-processed.png`)
      const preprocessSpec = {
        src: resolve(args.image),
        out: processed,
        ops: args.ops ?? [{ op: 'auto_orient' }, { op: 'autocontrast', cutoff: 1.0 }],
        max_side: 2000,
      }
      if (args.split) {
        preprocessSpec.split = args.split
        preprocessSpec.out_dir = join(outDir, `${base}-pieces`)
      }
      const pre = await runScript('preprocess.py', preprocessSpec, { timeoutMs: 120000 })
      const main = pre.main ?? processed
      const allOutputs = pre.outputs ?? []
      // 方向候选对照图是给主模型核对方向的，**不能**当成题面切块喂给第二读者（会误导它）
      const orientSheet = allOutputs.find((item) => item.kind === 'orient-sheet')
      const pieces = allOutputs
        .filter((item) => item.kind !== 'orient-sheet')
        .map((item) => item.path)
        .filter((path) => path !== main)

      let transcription
      let visionError
      if (args.second_reader !== false) {
        try {
          const visionSpec = { src: [main, ...pieces], timeout: 240 }
          if (args.prompt) visionSpec.prompt = args.prompt
          transcription = await runScript('vision.py', visionSpec, { timeoutMs: 300000 })
        } catch (error) {
          visionError = String(error?.message ?? error)
        }
      }

      const { image, note } = await saveImageRef(ctx, main)
      // 第二张图内联在同一次结果里：转正方向像素判不出来时，一眼就能确认
      let extraImage
      let extraImageNote
      if (orientSheet) {
        const savedSheet = await saveImageRef(ctx, orientSheet.path)
        extraImage = savedSheet.image
        extraImageNote = savedSheet.image
          ? '方向候选对照图：文字被判定为竖排（像素判方向对「横排/竖排」可靠，对「转 +90 还是 -90」'
            + '不可靠——两候选统计量只差 9%），故把两个候选都给你。主图用的是第一格（rotate +90）；'
            + '若另一格才是正的，再加 ops:[{"op":"rotate","degrees":180}]。'
          : `方向候选对照图未能内联（${savedSheet.note ?? '未知原因'}），请按路径 ${orientSheet.path} 查看。`
      }
      const failedProviders = (transcription?.attempts ?? [])
        .filter((item) => item.status !== 'ok' && item.status !== 'ok_after_repair')
        .map((item) => `${item.provider}=${item.status}`)
      const summaryLines = [
        `预处理完成：${main}`,
        pre.notes?.length ? `已做操作：${pre.notes.join('、')}` : '',
        pieces.length ? `分栏输出 ${pieces.length} 张：${pieces.join('、')}` : '',
        transcription
          ? (transcription.parsed
              ? `第二读者转写（${transcription.provider ?? '未知引擎'}，已结构化${transcription.repaired ? '，经整理重试' : ''}）：` +
                JSON.stringify(transcription.transcription)
              : `第二读者**未按 JSON 返回**（${transcription.provider ?? '未知引擎'}，已降级为原文，`
                + `原文通常仍可用，请按自然语言读）：${String(transcription.raw ?? '').slice(0, 1200)}`)
          : visionError
            ? `第二读者不可用（本次转写**完全来自主模型自己读图**，没有独立复核）：${visionError}`
            : '未做第二读者转写',
        failedProviders.length ? `第二读者链路：${failedProviders.join('、')}` : '',
      ].filter(Boolean)
      void exec
      return {
        ok: true,
        main,
        outputs: allOutputs,
        pieces,
        orient_sheet: orientSheet?.path ?? null,
        notes: pre.notes ?? [],
        transcription: transcription ?? null,
        vision_error: visionError ?? null,
        image,
        imageNote: note,
        extraImage,
        extraImageNote,
        summary: summaryLines.join('\n'),
      }
    },
  }))

  // 2) 读题自检：把转写渲染回图，与原图并排
  ctx.tools.register(defineTool({
    name: 'mech_verify_reading',
    description:
      '读题自检（任意学科）：把你转写的题面渲染成图，与原始照片并排输出一张对照图并回传图片，' +
      '用于逐个核对学生题目的文字、数值、单位、符号、支座/连接/元件、坐标轴与图中要素。' +
      '凡有疑问的数值都应先做这一步再解题。',
    parameters: {
      original: { type: 'string', required: true, description: '原始题目图片路径（通常用 mech_read_problem 的 main）' },
      transcription: { type: 'string', required: true, description: '你读出来的题面文字，公式可用 LaTeX（$...$）；可分块写"已知/求/图中要素/存疑"' },
      out: { type: 'string', description: '对照图输出路径；缺省 <workspace>/mech-images/compare-<时间戳>.png' },
      workspace: { type: 'string' },
      crop_original: { type: 'json', description: '对原图裁切区域 [l,t,r,b]（0–1 归一化）' },
      font_size: { type: 'number', description: '转写面板字号，缺省 22' },
    },
    timeoutMs: 180000,
    output: { schema: { type: 'json' }, render: (_args, value) => contentFrom(value, value.summary) },
    async execute(args) {
      const workspace = workspaceOf(args)
      const out = args.out ? resolve(args.out) : join(workspace, 'mech-images', `compare-${stamp()}.png`)
      const spec = {
        original: resolve(args.original),
        transcription: args.transcription,
        out,
      }
      if (args.crop_original) spec.crop_original = args.crop_original
      if (args.font_size) spec.font_size = args.font_size
      const result = await runScript('render_math.py', spec, { timeoutMs: 120000 })
      const { image, note } = await saveImageRef(ctx, result.out ?? out)
      return {
        ok: true,
        path: result.out ?? out,
        renderer: result.renderer ?? null,
        warnings: result.warnings ?? [],
        substitutions: result.substitutions ?? [],
        image,
        imageNote: note,
        summary: [
          `对照图已生成（渲染器 ${result.renderer ?? '未知'}）：${result.out ?? out}`,
          '请逐项核对：题干文字、每个数值与单位、支座类型与位置、载荷方向与大小、截面尺寸。',
          '凡两边读法不一致或低置信的字段，应向学生复述并请其确认后再解题。',
        ].join('\n'),
      }
    },
  }))

  // 3) 数值分析 + 不变量校核
  ctx.tools.register(defineTool({
    name: 'mech_analyze',
    description:
      '力学数值内核：按给定模型求支座反力、轴力 N、剪力 Q、弯矩 M、扭矩 T 的分布与极值，计算截面几何性质与最大应力，并做独立校核（ΣF=0、ΣM=0、dM/dx=Q、简支端 M=0、∫σdA=N、∫yσdA=M、单位与量级合理性）。静定问题才给结果；静不定会明确拒绝而不猜测。绘图请把结果交给 mech_diagram。',
    parameters: {
      spec: {
        type: 'json',
        required: true,
        description:
          '模型定义：{kind:"bar|beam|shaft", length, units, supports:[{x,type}], loads:[{type,x,value,dir}|{type:"distributed",x0,x1,q0,q1,dir}|{type:"point_moment",x,value}|{type:"point_torque",x,value}], section:{shape,params,E}, stations?}；符号约定见技能 engineering-mechanics',
      },
      workspace: { type: 'string' },
    },
    timeoutMs: 180000,
    output: {
      schema: { type: 'json' },
      render: (_args, value) => [{ type: 'text', text: value.summary }],
    },
    async execute(args) {
      const result = await runScript('analyze.py', args.spec ?? {}, { timeoutMs: 120000 })
      const checks = (result.checks ?? []).map(
        (check) => `${check.pass ? 'PASS' : 'FAIL'} ${check.name}${check.note ? ` (${check.note})` : ''}`,
      )
      const reactions = (result.reactions ?? [])
        .map((item) => `${item.type}@x=${item.x}: Rx=${item.Rx ?? 0}, Ry=${item.Ry ?? 0}, M=${item.M ?? 0}`)
        .join('；')
      const summary = [
        `kind=${result.kind} 静定=${result.determinate}`,
        reactions ? `反力：${reactions}` : '',
        result.extrema ? `极值：${extremaText(result.extrema)}` : '',
        result.stresses ? `应力：σmax=${result.stresses.sigma_max} @x=${result.stresses.sigma_max_x}，τmax=${result.stresses.tau_max}` : '',
        result.section ? `截面：A=${result.section.A}, Iy=${result.section.Iy}, W=${result.section.W}, Sy=${result.section.Sy}, Ip=${result.section.Ip}, Wp=${result.section.Wp}` : '',
        result.unsupported?.length ? `未覆盖：${result.unsupported.join('；')}` : '',
        result.warnings?.length ? `提示：${result.warnings.join('；')}` : '',
        checks.length ? `校核：${checks.join(' | ')}` : '',
        `原始结果 JSON：${JSON.stringify(result)}`,
      ]
        .filter(Boolean)
        .join('\n')
      return { ...result, summary }
    },
  }))

  // 4) 工程图：力学预设（跑在同一个通用引擎上，不是独立绘图器）
  ctx.tools.register(defineTool({
    name: 'mech_diagram',
    description:
      '画工程力学图并回传图片：轴力图、剪力图、弯矩图、扭矩图、应力分布图、莫尔应力圆、挠曲线。' +
      '它**不是独立的绘图器**：预设只是把「学科数据」展开成通用图元（线/填充/多边形/圆/文字/引线标注），' +
      '真正的渲染、排版、降级与质检与 viz_figure 共用同一套引擎，所以力学图不会有单独的排版 bug。' +
      '`preset`（旧写法 `kind` 等价）取 axial / shear / moment / torque / stress / mohr / deflection；' +
      '`panels` 可把 N/Q/M 多图共享 x 轴纵向并列（材料力学三图标准画法，只让最下面一张显示 x 刻度）。' +
      '分段常值用 segments，曲线用 points，应力分布用 dist，平面应力状态用 sigma_x/sigma_y/tau_xy。' +
      '图只呈现你给的数据，本身不做力学计算：反力与内力极值请先用 mech_analyze 算出来，再把它给的数据画出来。',
    parameters: {
      spec: {
        type: 'json',
        required: true,
        description:
          '图定义：{preset（或 kind）:"axial|shear|moment|torque|stress|mohr|deflection", unit, title, x_label, ' +
          'xlim, ylim, segments:[{x0,x1,value,label}], points:[{x,value}], dist:[{y,sigma}], ' +
          'sigma_x, sigma_y, tau_xy, neutral_axis, type:"linear|parabolic"（应力分布）, ' +
          'marks:[{x,value,text}], footnotes:[], panels:[...同结构...], width, height, dpi_scale, backend}。' +
          '例：{"preset":"moment","unit":"kN·m","points":[{"x":0,"value":0},{"x":2,"value":10},{"x":4,"value":0}]}；' +
          '{"preset":"mohr","sigma_x":50,"sigma_y":-10,"tau_xy":20,"unit":"MPa"}；' +
          '{"panels":[{"preset":"shear",...},{"preset":"moment",...}],"title":"剪力图与弯矩图"}。',
      },
      out: { type: 'string', description: 'PNG 输出路径；缺省 <workspace>/mech-diagrams/<preset>-<时间戳>.png' },
      workspace: { type: 'string' },
    },
    timeoutMs: 240000,
    output: { schema: { type: 'json' }, render: (_args, value) => contentFrom(value, value.summary) },
    async execute(args) {
      const workspace = workspaceOf(args)
      // 深拷贝：translateDiagramKeys 会把 kind 改写为 preset，不能side-effect 改到调用方传进来的对象
      const spec = translateDiagramKeys(JSON.parse(JSON.stringify(args.spec ?? {})))
      const kinds = diagramKinds(spec)
      const kind = kinds[0] ?? 'diagram'
      if (!spec.out) {
        spec.out = args.out
          ? resolve(workspace, args.out)
          : join(workspace, 'mech-diagrams', `${kind}-${stamp()}.png`)
      }
      const notes = []
      let python = null
      try {
        python = await resolvePython('matplotlib')
      } catch (error) {
        notes.push(
          `没有找到带 matplotlib 的解释器（${String(error?.message ?? error).slice(0, 160)}），本次走 Pillow 基础后端`,
        )
      }
      if (!python) python = await resolvePython('base')
      const result = await runScript('figure.py', spec, { timeoutMs: 180000, python })
      const { image, note } = await saveImageRef(ctx, result.out)
      // 必须用翻译后的 spec：diagramExtrema 读的是 panel.preset，
      // 传原始 spec（还是 kind）会一个都汇总不到，extrema 恒为 null
      const extrema = diagramExtrema(spec)
      const unit = spec.unit ?? spec.panels?.[0]?.unit ?? ''
      const summary = [
        `已生成 ${(kinds.length ? kinds : [kind]).join('、')} 图（${result.backend} 后端）：${result.out}（${result.width}x${result.height} px，${result.bytes} 字节）`,
        extremaText(extrema, unit) ? `图上数据的极值：${extremaText(extrema, unit)}` : '',
        '说明：这里的极值只描述"你给的数据画出来是什么样"，力学结论请引用 mech_analyze 的 extrema 与 checks。',
        result.unsupported?.length ? `该后端不支持（如实标注）：${result.unsupported.join('；')}` : '',
        result.warnings?.length ? `引擎提示：${result.warnings.join('；')}` : '',
        notes.length ? `提示：${notes.join('；')}` : '',
      ]
        .filter(Boolean)
        .join('\n')
      return {
        ok: true,
        path: result.out,
        backend: result.backend,
        width: result.width,
        height: result.height,
        bytes: result.bytes,
        kinds: kinds.length ? kinds : [kind],
        panels: result.panels ?? 1,
        extrema,
        unsupported: result.unsupported ?? [],
        warnings: [...(result.warnings ?? []), ...notes],
        image,
        imageNote: note,
        summary,
      }
    },
  }))

  // 5) 题目卡片
  ctx.tools.register(defineTool({
    name: 'mech_card',
    description:
      '题目卡片持久化：把一个题的图片路径、转写、假设、步骤、答案、校核结果、存疑点存成 JSON，之后（包括图片被上下文压缩省略之后）可按 id 取回。多轮追问、错题复习、批量刷题时使用。',
    parameters: {
      action: { type: 'string', required: true, description: 'save | get | list | append' },
      workspace: { type: 'string' },
      id: { type: 'string', description: '卡片 id（save 时可省，按 title 生成）' },
      card: { type: 'json', description: 'save 时的卡片字段：{id,title,source_images,transcription,given,find,assumptions,answers,doubts}' },
      field: { type: 'string', description: 'append 时的数组字段名，如 steps / doubts / checks / answers' },
      items: { type: 'json', description: 'append 时追加的条目（单个对象或数组）' },
    },
    timeoutMs: 30000,
    output: {
      schema: { type: 'json' },
      render: (_args, value) => [{ type: 'text', text: value.summary }],
    },
    async execute(args) {
      const options = { workspace: workspaceOf(args) }
      const action = String(args.action ?? '').toLowerCase()
      if (action === 'save') {
        const saved = await saveCard(args.card ?? {}, options)
        return { ok: true, path: saved.path, card: saved.card, summary: `卡片已保存：${saved.path}` }
      }
      if (action === 'get') {
        if (!args.id) throw new Error('get 需要 id')
        const found = await getCard(args.id, options)
        return { ok: true, path: found.path, card: found.card, summary: `卡片 ${args.id}：${JSON.stringify(found.card)}` }
      }
      if (action === 'list') {
        const listed = await listCards(options)
        return {
          ok: true,
          dir: listed.dir,
          cards: listed.cards,
          summary: listed.cards.length
            ? `共 ${listed.cards.length} 张卡片（${listed.dir}）：${listed.cards.map((card) => `${card.id}${card.title ? `(${card.title})` : ''}`).join('、')}`
            : `还没有卡片（目录 ${listed.dir}）`,
        }
      }
      if (action === 'append') {
        if (!args.id) throw new Error('append 需要 id')
        if (!args.field) throw new Error('append 需要 field')
        const appended = await appendCard(args.id, args.field, args.items ?? [], options)
        return {
          ok: true,
          path: appended.path,
          card: appended.card,
          summary: `已向 ${args.id} 的 ${args.field} 追加 ${Array.isArray(args.items) ? args.items.length : 1} 条：${appended.path}`,
        }
      }
      throw new Error(`未知 action：${args.action}（可用 save / get / list / append）`)
    },
  }))

  // 6) 通用可视化：学科无关的声明式出图（力学图以外的任何学科图都走这里）
  ctx.tools.register(defineTool({
    name: 'viz_figure',
    description:
      '通用出图工具（学科无关，不逐学科枚举图型）：给一份声明式 spec，引擎负责排版。坐标系支持直角/对数/极坐标/三维；系列支持折线·阶梯·散点·柱·填充·误差棒·向量场·等高线·热力图·三维曲面·函数曲线与参数曲线（受限表达式求值）；另有图元层（多边形·圆·椭圆·箭头·竖线横线·文字·引线标注）与多子图网格。matplotlib 优先，缺 matplotlib 时自动退回 Pillow 基础后端并如实列出不支持项。所有文字（标题、轴名、图例、文字标注、表格）都支持 mathtext：公式写在 $...$ 里，例如 "$\\frac{\\sin x}{x}$"、"$\\sigma_1$"、"$\\sum_{n=1}^{\\infty} a_n x^n$"，中文写在公式外面，不需要本机装 LaTeX。check=true 时出图后自动跑一次独立视觉评审。',
    parameters: {
      spec: {
        type: 'json',
        required: true,
        description:
          '图定义：{out?,title?,subtitle?,note?:[],width?,height?,dpi_scale?,backend?:"auto|matplotlib|pillow",grid?:{rows,cols},axes:[{slot?:[r,c],type?:"cartesian|logx|logy|loglog|polar",xlim?,ylim?,xlabel?,ylabel?,title?,grid?,legend?,aspect?:"equal",xticks?,yticks?,unit?,series:[...]}]}。series 形如 {kind,label?,color?,alpha?,...}：line/step/scatter/area 用 x[]+y[]，bar 加 bar_width，errorbar 加 yerr[]，quiver 用 x/y/u/v，contour 用 x/y/z[][]（levels,cmap,filled），heatmap 用 z[][]，surface3d 用 x/y/z[][]（view:[elev,azim]），function 用 expr+xrange，parametric 用 fx/fy+trange，polygon 用 xy[[]]，circle/ellipse 用 center+r 或 width/height，arrow 用 xy+xytext，vline/hline 用 x 或 y，text 用 xy+text，annotate 用 xy+text+xytext。数学排版：math_fontset?:"dejavusans|dejavuserif|cm|stix|stixsans"（cm/stix 是论文里常见的衬线数学字形），font_family?:字符串或数组（覆盖默认字体链，默认链末尾有 DejaVu Sans 兜底，缺字形不会画成方框），font_fallback?:false 可关掉兜底，latex?:true|false|"auto"（真 LaTeX：需要本机装 latex+dvipng，缺就明确报错；没装时用默认 mathtext 即可）。',
      },
      out: { type: 'string', description: 'PNG 输出路径；缺省 <workspace>/figures/figure-<时间戳>.png' },
      backend: { type: 'string', description: 'auto | matplotlib | pillow（缺省 auto：matplotlib 优先，缺则退回 pillow）' },
      python: { type: 'string', description: '显式指定解释器路径（缺省自动探测带 matplotlib 的解释器，再用带 numpy+Pillow 的）' },
      check: { type: 'boolean', description: '出图后自动调用独立视觉模型质检图面（文字重叠/裁切/自洽性），结论一并返回' },
      qa_provider: { type: 'string', description: '质检引擎：zhipu-glm / deepseek / siliconflow / sensenova / gemini（缺省按引擎链故障转移）' },
      workspace: { type: 'string' },
    },
    timeoutMs: 300000,
    output: { schema: { type: 'json' }, render: (_args, value) => contentFrom(value, value.summary) },
    async execute(args) {
      const workspace = workspaceOf(args)
      const spec = { ...(args.spec ?? {}) }
      if (!spec.out) {
        spec.out = args.out
          ? resolve(workspace, args.out)
          : join(workspace, 'figures', `figure-${stamp()}.png`)
      }
      const requested = String(args.backend ?? spec.backend ?? 'auto')
      const notes = []
      let python = args.python ? resolve(workspace, args.python) : null
      if (!python && requested !== 'pillow') {
        try {
          python = await resolvePython('matplotlib')
        } catch (error) {
          notes.push(
            `没有找到带 matplotlib 的解释器（${String(error?.message ?? error).slice(0, 200)}），本次走 Pillow 基础后端`,
          )
        }
      }
      if (!python) python = await resolvePython('base')
      const result = await runScript('figure.py', spec, { timeoutMs: 240000, python })
      if (requested !== 'auto' && result.backend !== requested) {
        notes.push(`请求后端 ${requested}，实际后端 ${result.backend}`)
      }
      const { image, note } = await saveImageRef(ctx, result.out)
      let review = null
      let reviewError = null
      if (args.check) {
        try {
          review = await visionReview(result.out, { provider: args.qa_provider })
        } catch (error) {
          reviewError = String(error?.message ?? error)
        }
      }
      const summary = [
        `已出图（${result.backend} 后端）：${result.out}（${result.width}x${result.height} px，${result.bytes} 字节）`,
        `系列：${(result.kinds ?? []).join('、') || '无'}；子图 ${result.panels ?? 1} 个`,
        result.unsupported?.length
          ? `该后端不支持（如实标注，未静默降级）：${result.unsupported.join('；')}`
          : '',
        result.warnings?.length ? `引擎提示：${result.warnings.join('；')}` : '',
        notes.length ? `提示：${notes.join('；')}` : '',
        review
          ? `视觉评审：${review.verdict}（${review.provider ?? '未知引擎'}）\n${review.text}`
          : reviewError
            ? `视觉评审不可用：${reviewError}`
            : '',
      ]
        .filter(Boolean)
        .join('\n')
      return {
        ok: true,
        path: result.out,
        backend: result.backend,
        width: result.width,
        height: result.height,
        bytes: result.bytes,
        kinds: result.kinds ?? [],
        panels: result.panels ?? 1,
        unsupported: result.unsupported ?? [],
        warnings: [...(result.warnings ?? []), ...notes],
        review,
        review_error: reviewError,
        image,
        imageNote: note,
        summary,
      }
    },
  }))

  // 7) 独立视觉评审：我看不见图时的代看通道（也用于读题图与结构化证据）
  ctx.tools.register(defineTool({
    name: 'viz_check',
    description:
      '独立视觉评审：把一张图交给视觉模型，按固定质检清单返回结论——文字是否完整无乱码、有无文字压图/标注重叠/越界/裁切、图形是否自洽（轴名单位刻度数据图例是否一致）、最影响可读性的问题、PASS 或 FAIL。用于出图后自检，也用于读题图（给 prompt 即可得到转写与结构化证据）。',
    parameters: {
      image: { type: 'string', required: true, description: '待检查图片路径（相对工作区或绝对路径）' },
      prompt: { type: 'string', description: '自定义提问（需自行要求模型只输出 JSON）；缺省用内置图面质检清单' },
      provider: { type: 'string', description: '视觉引擎：zhipu-glm / deepseek / siliconflow / sensenova / gemini' },
      workspace: { type: 'string' },
    },
    timeoutMs: 300000,
    output: {
      schema: { type: 'json' },
      render: (_args, value) => [{ type: 'text', text: value.summary }],
    },
    async execute(args) {
      const workspace = workspaceOf(args)
      const path = resolve(workspace, args.image)
      if (!existsSync(path)) throw new Error(`图片不存在：${path}`)
      const review = await visionReview(path, { provider: args.provider, prompt: args.prompt })
      const summary = [
        `视觉评审（${review.provider ?? '未知引擎'}${review.model ? ` / ${review.model}` : ''}）：${review.verdict}`,
        review.parsed ? '' : '（引擎未按 JSON 返回，以下为原文）',
        review.text,
      ]
        .filter(Boolean)
        .join('\n')
      return {
        ok: true,
        path,
        verdict: review.verdict,
        provider: review.provider,
        model: review.model,
        parsed: review.parsed,
        evidence: review.evidence,
        raw: review.raw,
        attempts: review.attempts,
        summary,
      }
    },
  }))

  // 8) 原图标注：在学生的原图上圈重点、画箭头、加尺寸线、局部放大
  ctx.tools.register(defineTool({
    name: 'viz_annotate',
    description:
      '在**原图**上做讲解式标注并回传图片：0–1 归一化坐标的矩形框/高亮/圆/椭圆/箭头/直线/尺寸线/文字，' +
      '以及 zoom 局部放大（把小字区域放大后贴回图上并引线相连）和 grid 坐标网格。' +
      '用途：讲解学生给的题图（受力图、电路图、几何图、图表、流程图）时在图上指出关键部位；' +
      '或先用 grid 出一张带 0–1 网格的图定位目标，再用精确坐标标注。' +
      '本工具只负责画，不做任何物理判断；坐标默认是相对当前底图的 0–1 比例（左上角为原点，x 向右，y 向下），' +
      '给 px:true 则用像素。' +
      '支持的 op：crop / box / highlight / circle / ellipse / arrow / line / dimension / text / zoom / grid / invert / grayscale。',
    parameters: {
      src: { type: 'string', required: true, description: '原图路径（绝对路径，或相对 workspace）' },
      ops: {
        type: 'json',
        description:
          '标注操作数组，按顺序作用在当前底图上。例：[{"op":"grid","cols":10,"rows":10},' +
          '{"op":"box","box":[0.1,0.2,0.4,0.5],"label":"危险截面"},' +
          '{"op":"arrow","from":[0.8,0.9],"to":[0.5,0.6],"label":"P=10kN"},' +
          '{"op":"dimension","from":[0.1,0.7],"to":[0.9,0.7],"text":"L = 4 m","offset":[0,-0.03]},' +
          '{"op":"zoom","box":[0.05,0.5,0.5,0.95],"factor":3,"at":"br","label":"放大"}]。' +
          'crop 之后所有坐标相对新底图；zoom 的 at 可用 tl/tr/bl/br 或 [x,y]。',
      },
      out: { type: 'string', description: '输出 PNG 路径；缺省 <workspace>/mech-images/annotated-<时间戳>.png' },
      note: { type: 'json', description: '底部说明文字数组（最多 4 行）' },
      workspace: { type: 'string' },
    },
    timeoutMs: 180000,
    output: { schema: { type: 'json' }, render: (_args, value) => contentFrom(value, value.summary) },
    async execute(args) {
      const workspace = workspaceOf(args)
      if (!args.src || typeof args.src !== 'string') {
        throw new Error('viz_annotate 需要 src（原图路径，绝对路径或相对 workspace）')
      }
      const out = args.out
        ? resolve(workspace, args.out)
        : join(workspace, 'mech-images', `annotated-${stamp()}.png`)
      const spec = {
        src: resolve(workspace, args.src),
        out,
        ops: args.ops ?? [],
      }
      if (args.note) spec.note = args.note
      const result = await runScript('annotate.py', spec, { timeoutMs: 120000 })
      const { image, note } = await saveImageRef(ctx, result.out ?? out)
      const summary = [
        `已在原图上标注：${result.out ?? out}（${result.width}x${result.height} px，${result.bytes} 字节）`,
        `用到：${(result.kinds ?? []).join('、') || '无'}`,
        (result.notes ?? []).length ? `说明：${result.notes.join('；')}` : '',
        (result.warnings ?? []).length ? `提示：${result.warnings.join('；')}` : '',
        (result.notes ?? []).some((item) => String(item).startsWith('grid'))
          ? '这是坐标网格图：读出路标刻度后，再用精确 0–1 坐标重新调用本工具做正式标注。'
          : '',
      ].filter(Boolean).join('\n')
      return {
        ok: true,
        path: result.out ?? out,
        width: result.width,
        height: result.height,
        bytes: result.bytes,
        kinds: result.kinds ?? [],
        notes: result.notes ?? [],
        warnings: result.warnings ?? [],
        image,
        imageNote: note,
        summary,
      }
    },
  }))

  // 9) 通用求解内核：学科无关的符号/数值求解 + 独立验算
  ctx.tools.register(defineTool({
    name: 'solve_math',
    description:
      '通用求解内核（学科无关）：把工程/数学里的方程、表达式、微分方程、矩阵交给它做**确定性求解与独立验算**。' +
      'op：solve（方程/方程组）、simplify/expand/factor/together/apart、diff、integrate（不定/定）、limit、series、' +
      'dsolve（ODE，可带初值）、matrix（det/inverse/eigen/rank/rref/transpose/solve/power）、evaluate、roots、' +
      'optimize、fit（最小二乘）、table（数值表，可直接喂给 viz_figure）、probe（本机能力自检）。' +
      '返回精确解、LaTeX、数值近似，以及**独立复核的定量残差**（代回原式、求导回代、与数值积分/差分对比、‖Av−λv‖ 等）。' +
      '有 sympy 时走精确符号路径；没有时用 numpy 数值降级，并把做不到的 op 如实列进 unsupported（绝不编造精确解）。',
    parameters: {
      spec: {
        type: 'json',
        required: true,
        description:
          '求解定义：{"op":"...", ...参数}。例：' +
          '{"op":"solve","equations":["x^2-3*x+2=0"],"unknowns":["x"]}；' +
          '{"op":"integrate","expr":"x^2*exp(x)","var":"x","limits":[0,1]}；' +
          '{"op":"dsolve","ode":"y\'\' + 2*y\' + y = 0","func":"y","ics":{"y(0)":1,"y\'(0)":0}}；' +
          '{"op":"matrix","a":[[2,1],[1,3]],"op":"eigen"}；' +
          '{"op":"fit","x":[0,1,2,3],"y":[1.1,2.9,5.2,6.8],"degree":1}。' +
          '可选 digits（有效位）。',
      },
      workspace: { type: 'string' },
    },
    timeoutMs: 300000,
    output: {
      schema: { type: 'json' },
      render: (_args, value) => [{ type: 'text', text: value.summary }],
    },
    async execute(args) {
      const spec = args.spec ?? {}
      // 先找带 sympy 的解释器（随包 .pylibs 里有 sympy）；找不到就退回基础解释器，
      // 由 symbolic.py 自己降级并如实上报 —— 这样"没有 sympy"不会让整个工具不可用。
      let python = null
      let symbolic = false
      try {
        python = await resolvePython('numpy,sympy')
        symbolic = true
      } catch (error) {
        python = await resolvePython('base')
        void error
      }
      const result = await runScript('symbolic.py', spec, { timeoutMs: 240000, python })
      const exact = (result.exact ?? []).filter((item) => item !== null && item !== undefined)
      const numeric = (result.numeric ?? []).filter((item) => item !== null && item !== undefined)
      const checks = (result.checks ?? []).map(
        (check) => `${check.pass ? 'PASS' : 'FAIL'} ${check.name}${check.note ? ` (${check.note})` : ''}`,
      )
      const summary = [
        `op=${result.op ?? spec.op}　解释器：${symbolic ? '带 sympy（精确符号路径）' : '无 sympy（数值降级路径）'}`,
        exact.length ? `精确解：${exact.join('；')}` : '',
        numeric.length ? `数值：${numeric.map((v) => (typeof v === 'number' ? String(v) : JSON.stringify(v))).join('；')}` : '',
        (result.latex ?? []).length ? `LaTeX：${result.latex.join('　')}` : '',
        (result.steps ?? []).length
          ? `过程：${result.steps.map(describeStep).filter(Boolean).join('；')}` : '',
        result.unsupported?.length ? `本环境做不到（如实标注）：${result.unsupported.join('；')}` : '',
        result.warnings?.length ? `提示：${result.warnings.join('；')}` : '',
        checks.length ? `验算：${checks.join(' | ')}` : '',
        `原始结果 JSON：${JSON.stringify(result)}`,
      ].filter(Boolean).join('\n')
      return { ...result, python, sympy: symbolic, summary }
    },
  }))
}

// --------------------------------------------------------------------------
// 插件入口
// --------------------------------------------------------------------------

/** Cordis 插件名。 */
export const name = PROVIDER_NAME
/** 工具注册表是本插件唯一的硬依赖；attachments 与 skills 都按可选取用。 */
export const inject = ['tools']

/**
 * 注册工具与随包技能。
 *
 * @param ctx - 插件上下文。
 */
export function apply(ctx) {
  registerTools(ctx)
  try {
    const skills = typeof ctx.get === 'function' ? ctx.get('skills') : ctx.skills
    if (skills && typeof skills.registerProvider === 'function') {
      skills.registerProvider(createSkillProvider)
    }
  } catch (error) {
    // 技能目录有问题时只降级技能，工具仍须可用。
    ctx.logger?.warn?.('dsh-mech-workbench: 技能注册失败：%s', error?.message ?? error)
  }
}