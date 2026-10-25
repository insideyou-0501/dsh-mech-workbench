/**
 * 离线自测：用 mock ctx 加载插件，逐个调用工具，核对结果与脚本契约。
 *
 * 运行（工作区 Node）：
 *   node mech-workbench/test/selftest.mjs
 *
 * 不联网、不调用视觉模型（`mech_read_problem` 固定 `second_reader:false`），
 * 只验证「插件入口 ↔ 四个 Python 脚本」的参数与返回字段是否真的对得上。
 */

import { mkdir, rm, stat, readFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { assertHostSchemaSubset } from './host-schema-subset.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = dirname(HERE)
const TMP = join(HERE, 'tmp')

const results = []
function record(name, ok, detail = '') {
  results.push({ name, ok, detail })
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  — ${detail}` : ''}`)
}
function approx(actual, expected, tol = 1e-6) {
  return typeof actual === 'number' && Math.abs(actual - expected) <= tol * Math.max(1, Math.abs(expected))
}

// ---- 加载插件（mock ctx） ----
const registered = new Map()
const providerCalls = []
/**
 * mock 附件服务。默认只返回**部分字段**（没有 width/height），刻意模拟"服务实现
 * 只给一部分字段"的部署；工具必须先净化再用，否则 `{width: undefined}` 会让
 * 整次调用被判「非无损 JSON」。测试里可以临时替换 `saveImage` 去走完整引用形状。
 */
const mockAttachments = {
  imageLimits: { maxImageBytes: 20 * 1024 * 1024 },
  async saveImage({ data, mediaType, name }) {
    return { attachmentId: `mock-${name}`, mediaType, bytes: data?.byteLength ?? data?.length ?? 0 }
  },
}
const schemaViolations = []
const ctx = {
  tools: {
    register(definition) {
      // 宿主会对 parameters / output.schema 做原始 JSON Schema 子集校验；
      // mock registry 若只收集不校验，`defineTool` 的退化分支在自测里永远无害
      // （issue #3：作者 DSL 原样进注册表 → JsonSchemaError → 插件不激活）。
      try {
        assertHostSchemaSubset(definition.parameters, `${definition.name}.parameters`)
        if (definition.output?.schema !== undefined) {
          assertHostSchemaSubset(definition.output.schema, `${definition.name}.output.schema`)
        }
      } catch (error) {
        schemaViolations.push({ tool: definition.name, message: error.message })
      }
      registered.set(definition.name, definition)
    },
  },
  get(key) {
    if (key === 'skills') {
      return {
        registerProvider(factory) {
          providerCalls.push(factory)
        },
      }
    }
    if (key === 'attachments') return mockAttachments
    return undefined
  },
  logger: { warn() {}, info() {} },
}

const plugin = await import(pathToFileURL(join(ROOT, 'index.js')).href)
plugin.apply(ctx, {})

const EXPECTED = [
  'mech_read_problem',
  'mech_verify_reading',
  'mech_analyze',
  'mech_diagram',
  'mech_card',
  'viz_figure',
  'viz_check',
  'viz_annotate',
  'solve_math',
]
record(
  `插件加载并注册 ${EXPECTED.length} 个工具`,
  EXPECTED.every((name) => registered.has(name)) && registered.size === EXPECTED.length,
  `已注册 ${[...registered.keys()].join(', ')}`,
)
record('export 形态正确（name/inject/apply）',
  plugin.name === 'dsh-mech-workbench' && Array.isArray(plugin.inject) && typeof plugin.apply === 'function',
  `name=${plugin.name} inject=${JSON.stringify(plugin.inject)}`)

record(
  `工具定义通过宿主 JSON Schema 子集（${EXPECTED.length} 个 parameters + output.schema）`,
  schemaViolations.length === 0,
  schemaViolations.length
    ? schemaViolations.map((item) => `${item.tool}: ${item.message}`).join(' | ')
    : '没有越界关键字，也没有残留的作者专用 type:"json"',
)

record('随包技能提供方被注册', providerCalls.length === 1, `registerProvider 调用 ${providerCalls.length} 次`)
if (providerCalls.length === 1) {
  const provider = providerCalls[0]()
  const list = await provider.list()
  const SKILLS = [
    'engineering-mechanics',
    'engineering-problem-solving',
    'problem-image-reading',
    'engineering-visualization',
  ]
  const missing = SKILLS.filter((name) => !list.some((item) => item.name === name))
  record(
    `随包技能齐全（${SKILLS.length} 个：读图/解题/可视化/力学约定）`,
    missing.length === 0,
    missing.length ? `缺少 ${missing.join(', ')}` : list.map((item) => item.name).join(', '),
  )
  for (const name of SKILLS) {
    const entry = list.find((item) => item.name === name)
    if (!entry) continue
    const full = await provider.get(entry)
    const body = full.content ?? ''
    // 每个技能都必须有 frontmatter 描述和正文，不能是空壳
    record(`技能正文可读取：${name}`, body.length > 200, `${body.length} 字符`)
  }
  const mechanics = list.find((item) => item.name === 'engineering-mechanics')
  if (mechanics) {
    const full = await provider.get(mechanics)
    record('力学技能含符号约定（拉为正）', /拉为正/.test(full.content ?? ''), `${(full.content ?? '').length} 字符`)
  }
}

/**
 * 复刻 DSH 对工具返回值的「无损 JSON」校验（dsh-util-values 的 walkJsonValue）。
 *
 * 真实运行时返回值过不了这一关，整次调用直接失败：
 *   tool "xxx" returned invalid output: value is not lossless JSON
 * 而 mock ctx 是直接调 execute、不过 DSH 校验的——所以必须在这里自己复刻一份，
 * 否则 `{ image: undefined }` 这种「工具其实成功了、却整次被判非法」的问题
 * 永远测不出来（真实踩过：附件服务不可用时三个回传图片的工具全废）。
 */
function losslessProblems(value, path = '$') {
  const problems = []
  const ancestors = new Set()
  const walk = (node, at) => {
    if (node === undefined) {
      problems.push(`${at} 是 undefined（对象里带这个键就不合法）`)
      return
    }
    if (node === null || typeof node === 'boolean' || typeof node === 'string') return
    if (typeof node === 'number') {
      if (!Number.isFinite(node)) problems.push(`${at} 是非有限数字 ${node}`)
      else if (Object.is(node, -0)) problems.push(`${at} 是 -0`)
      return
    }
    if (typeof node !== 'object') {
      problems.push(`${at} 类型 ${typeof node} 无法 JSON 表示`)
      return
    }
    if (ancestors.has(node)) {
      problems.push(`${at} 存在环`)
      return
    }
    ancestors.add(node)
    if (Array.isArray(node)) {
      if (Object.getPrototypeOf(node) !== Array.prototype) problems.push(`${at} 数组原型不是原生 Array`)
      if (Reflect.ownKeys(node).length !== node.length + 1) problems.push(`${at} 稀疏数组或带额外键`)
      for (let index = 0; index < node.length; index += 1) {
        if (!Object.prototype.hasOwnProperty.call(node, index)) problems.push(`${at}[${index}] 是空洞`)
        else walk(node[index], `${at}[${index}]`)
      }
    } else {
      const prototype = Object.getPrototypeOf(node)
      if (prototype !== null && prototype !== Object.prototype) {
        problems.push(`${at} 不是纯对象（原型 ${prototype?.constructor?.name ?? 'null'}），类实例不行`)
      }
      for (const key of Reflect.ownKeys(node)) {
        if (typeof key !== 'string') {
          problems.push(`${at} 含 symbol 键`)
          continue
        }
        if (!Object.prototype.propertyIsEnumerable.call(node, key)) {
          problems.push(`${at}.${key} 不可枚举`)
          continue
        }
        walk(node[key], `${at}.${key}`)
      }
    }
    ancestors.delete(node)
  }
  walk(value, path)
  return problems
}

/** 把校验失败变成一条可读的中文失败原因。 */
function assertLossless(toolName, surface, value) {
  const problems = losslessProblems(value)
  if (problems.length) {
    throw new Error(
      `${toolName} 的 ${surface} 不是无损 JSON（DSH 会拒绝整次调用）：`
        + `${problems.slice(0, 5).join('；')}${problems.length > 5 ? ` …共 ${problems.length} 处` : ''}`,
    )
  }
  return value
}

async function run(toolName, args, extraCtx) {
  const definition = registered.get(toolName)
  if (!definition) throw new Error(`工具未注册：${toolName}`)
  return assertLossless(toolName, '返回值', await definition.execute(args, extraCtx ?? {}))
}

await mkdir(TMP, { recursive: true })
const workspace = TMP

// ---- 1) mech_analyze：简支梁解析解 ----
try {
  const value = await run('mech_analyze', {
    workspace,
    spec: {
      kind: 'beam',
      length: 4,
      supports: [{ x: 0, type: 'pin' }, { x: 4, type: 'roller' }],
      loads: [{ type: 'point_force', x: 2, value: -10, dir: 'transverse' }],
      section: { shape: 'rect', params: { b: 0.1, h: 0.2 } },
    },
  })
  const ry = value.reactions.map((item) => item.Ry)
  const ok = value.determinate === true
    && approx(ry[0], 5) && approx(ry[1], 5)
    && approx(value.extrema.M.max, 10) && approx(value.extrema.M.max_x, 2)
    && approx(value.extrema.Q.max, 5)
    && approx(value.stresses.sigma_max, 15, 1e-6)
    && (value.checks ?? []).every((check) => check.pass)
  record('mech_analyze 简支梁（R=5/5、Mmax=10@2、σmax=15 MPa、全部校核通过）', ok,
    `R=[${ry.join(', ')}] Mmax=${value.extrema.M.max}@${value.extrema.M.max_x} σ=${value.stresses.sigma_max} checks=${value.checks.length}`)
} catch (error) {
  record('mech_analyze 简支梁', false, String(error?.message ?? error))
}

// ---- 1b) 变形口径：不许暗示"给了 E 就能算挠度" ----
// 本工具**不做任何变形计算**，E 只被记录。原先只在"未给 E"时警告"挠度无法计算"，
// 读起来像"给了 E 就能算"——这是误导。两种情形都必须说清"不计算变形"。
try {
  const spec = (withE) => ({
    workspace,
    spec: {
      kind: 'beam',
      length: 4,
      supports: [{ x: 0, type: 'pin' }, { x: 4, type: 'roller' }],
      loads: [{ type: 'point_force', x: 2, value: -10, dir: 'transverse' }],
      section: withE
        ? { shape: 'rect', params: { b: 0.1, h: 0.2 }, E: 200000000 }
        : { shape: 'rect', params: { b: 0.1, h: 0.2 } },
    },
  })
  const without = await run('mech_analyze', spec(false))
  const withE = await run('mech_analyze', spec(true))
  const says = (value) => (value.warnings ?? []).some((text) => /不计算变形/.test(text))
  record('mech_analyze 明确声明"不计算变形"（给不给 E 都一样，不暗示能算挠度）',
    says(without) && says(withE)
      && !(withE.warnings ?? []).some((text) => /无法计算/.test(text))
      && withE.section.E === 200000000,
    `无E：${(without.warnings ?? [])[0] ?? '（无）'} ／ 有E：${(withE.warnings ?? [])[0] ?? '（无）'}`)
} catch (error) {
  record('mech_analyze 变形口径', false, String(error?.message ?? error))
}

// ---- 2) mech_analyze：静不定必须明确拒绝 ----
try {
  const value = await run('mech_analyze', {
    workspace,
    spec: {
      kind: 'beam',
      length: 4,
      supports: [{ x: 0, type: 'fixed' }, { x: 4, type: 'roller' }],
      loads: [{ type: 'point_force', x: 2, value: -10, dir: 'transverse' }],
      section: { shape: 'rect', params: { b: 0.1, h: 0.2 } },
    },
  })
  record('mech_analyze 静不定如实报告（不编造内力）',
    value.determinate === false && (value.unsupported ?? []).length > 0 && !(value.diagrams?.M?.length > 0),
    (value.unsupported ?? []).join('；'))
} catch (error) {
  record('mech_analyze 静不定如实报告', false, String(error?.message ?? error))
}

// ---- 3) mech_diagram：轴力图（学生明确要求的能力） ----
try {
  const value = await run('mech_diagram', {
    workspace,
    spec: {
      kind: 'axial',
      unit: 'kN',
      segments: [{ x0: 0, x1: 1, value: 20 }, { x0: 1, x1: 2, value: -10 }],
      footnotes: ['拉为正'],
    },
  })
  const info = await stat(value.path)
  const png = await readFile(value.path)
  const isPng = png[0] === 0x89 && png[1] === 0x50 && png[2] === 0x4e && png[3] === 0x47
  const blocks = assertLossless('mech_diagram', '渲染内容块', registered.get('mech_diagram').output.render({}, value))
  record('mech_diagram 轴力图（PNG 落盘、极值回传、内容块可渲染）',
    value.ok === true && info.size > 10000 && isPng && value.extrema.axial.max === 20 && value.extrema.axial.min === -10
      && blocks[0].type === 'text',
    `${info.size} 字节 ${value.width}x${value.height} extrema=${JSON.stringify(value.extrema.axial)}`)
} catch (error) {
  record('mech_diagram 轴力图', false, String(error?.message ?? error))
}

// ---- 3b) 图片内联：完整引用形状（含类实例 originalDimensions） ----
// 这一路正是线上出问题的地方：mock 默认只给部分字段，而真实附件服务给的是完整
// 引用。两条路都必须产出无损 JSON，否则工具明明成功、整次调用却被 DSH 判失败。
try {
  class FakeDimensions {
    constructor(width, height) {
      this.width = width
      this.height = height
    }
  }
  const saved = []
  const originalSave = mockAttachments.saveImage
  mockAttachments.saveImage = async (input) => {
    saved.push(input)
    return {
      attachmentId: 'sha256:selftest',
      mediaType: input.mediaType,
      width: 1400,
      height: 868,
      bytes: 12345,
      name: input.name,
      // 故意给成**类实例**：官方 referenceImageValue 会再浅拷一层，
      // 我们必须同样处理，否则返回值被判「不是纯对象」而整次失败
      originalDimensions: new FakeDimensions(2800, 1800),
    }
  }
  let value
  let blocks
  try {
    value = await run('mech_diagram', {
      workspace,
      spec: { preset: 'axial', unit: 'kN', segments: [{ x0: 0, x1: 1, value: 5 }] },
    })
    blocks = assertLossless(
      'mech_diagram（内联）',
      '渲染内容块',
      registered.get('mech_diagram').output.render({}, value),
    )
  } finally {
    mockAttachments.saveImage = originalSave
  }
  const image = value.image
  record('图片内联路径（完整引用）：引用是纯对象、内容块带 image',
    value.ok === true
      && saved.length === 1
      && !!image && Object.getPrototypeOf(image) === Object.prototype
      && image.attachmentId === 'sha256:selftest'
      && Object.getPrototypeOf(image.originalDimensions) === Object.prototype
      && blocks.some((block) => block.type === 'image'
        && block.attachment.attachmentId === 'sha256:selftest'),
    `saveImage 调用 ${saved.length} 次，attachmentId=${image?.attachmentId}，`
      + `originalDimensions 原型=${image?.originalDimensions
        ? Object.getPrototypeOf(image.originalDimensions).constructor?.name : '无'}`)
} catch (error) {
  record('图片内联路径（完整引用）', false, String(error?.message ?? error))
}

// ---- 3b2) 对照：证明上面那道检查不是空转 ----
// mock 默认的"部分字段"引用会让未净化的返回值带 undefined 键——这正是线上
// 三个工具一起失败的原因。这里断言"未净化会被判非法"，否则 3b 的通过没有意义。
try {
  const unSanitized = { ok: true, image: { attachmentId: 'mock-x.png', mediaType: 'image/png', width: undefined, height: undefined } }
  const problems = losslessProblems(unSanitized)
  record('对照：未净化的 {width: undefined} 确实会被判非法（证明检查有效）',
    problems.length >= 2 && problems.some((text) => text.includes('undefined')),
    problems.slice(0, 2).join('；'))
} catch (error) {
  record('对照：未净化会被判非法', false, String(error?.message ?? error))
}

// ---- 3c) 净化器本身：undefined 键、NaN/Infinity/-0、类实例 ----
try {
  const { toLossless } = await import(pathToFileURL(join(ROOT, 'lib', 'images.js')).href)
  class Box {
    constructor() {
      this.width = 3
    }
  }
  const cleaned = toLossless({
    keep: 1,
    drop: undefined,
    nan: NaN,
    inf: Infinity,
    negZero: -0,
    boxed: new Box(),
    list: [1, undefined, -0],
  })
  record('toLossless：丢 undefined 键、规范 NaN/Infinity/-0、拍平类实例',
    losslessProblems(cleaned).length === 0
      && !('drop' in cleaned)
      && cleaned.nan === null && cleaned.inf === null && cleaned.negZero === 0
      && Object.getPrototypeOf(cleaned.boxed) === Object.prototype && cleaned.boxed.width === 3
      && cleaned.list[1] === null && cleaned.list[2] === 0,
    JSON.stringify(cleaned))
} catch (error) {
  record('toLossless 净化器', false, String(error?.message ?? error))
}

// ---- 4) mech_diagram：Q/M 双图共享 x 轴（梁类题标准画法） ----
try {
  const value = await run('mech_diagram', {
    workspace,
    spec: {
      panels: [
        { kind: 'shear', unit: 'kN', segments: [{ x0: 0, x1: 2, value: 5 }, { x0: 2, x1: 4, value: -5 }] },
        { kind: 'moment', unit: 'kN*m', points: [{ x: 0, value: 0 }, { x: 2, value: 10 }, { x: 4, value: 0 }] },
      ],
      x_label: 'x (m)',
    },
  })
  record('mech_diagram Q/M 双图（共享 x 轴、两个 kind 回传）',
    value.ok === true && value.kinds.join(',') === 'shear,moment' && value.extrema.moment.max === 10,
    `kinds=${value.kinds.join(',')} ${value.width}x${value.height}`)
} catch (error) {
  record('mech_diagram Q/M 双图', false, String(error?.message ?? error))
}

// ---- 5) mech_read_problem：预处理链路（离线，不调视觉模型） ----
try {
  const source = join(HERE, 'cases', 'q1_beam.png')
  const value = await run('mech_read_problem', {
    image: source,
    workspace,
    out_dir: join(TMP, 'images'),
    second_reader: false,
    ops: [{ op: 'auto_orient' }, { op: 'autocontrast', cutoff: 1.0 }],
  })
  const info = await stat(value.main)
  record('mech_read_problem 预处理（写主图、返回 outputs、未调视觉模型）',
    value.ok === true && info.size > 1000 && (value.outputs ?? []).length >= 1 && value.transcription === null,
    `${value.main} ${info.size} 字节；notes=${(value.notes ?? []).length} 条`)
} catch (error) {
  record('mech_read_problem 预处理', false, String(error?.message ?? error))
}

// ---- 5b) mech_read_problem：文字竖排 + 无 EXIF（线上真实故障形状）----
// 只测"EXIF 方向=6 必须建议 auto_orient"是**非判别性**的：它把"EXIF 一定可用"当成了前提，
// 而 DSH 的附件归一化恰好会把 EXIF 剥掉。这里从"没有 EXIF"的竖排图出发，锁三件事：
//   1) 真的按像素判出竖排并转正（主图宽高互换）；
//   2) 方向候选对照图内联进**同一次**工具结果（第二张图），模型不必再多花一轮去看；
//   3) 对照图**不许**被当成题面切块喂给第二读者（这是 index.js 的 kind 过滤，只有这里能测）。
try {
  const { contentFrom } = await import(pathToFileURL(join(ROOT, 'lib', 'images.js')).href)
  const value = await run('mech_read_problem', {
    image: join(HERE, 'cases', 'q5_text_rot90.png'),
    workspace,
    out_dir: join(TMP, 'images-rot'),
    second_reader: false,
    ops: [{ op: 'auto_orient' }],
  })
  const sheet = (value.outputs ?? []).find((item) => item.kind === 'orient-sheet')
  const sheetExists = value.orient_sheet ? existsSync(value.orient_sheet) : false
  const imageParts = contentFrom(value, value.summary ?? '').filter((part) => part.type === 'image')
  const mainInfo = await stat(value.main)
  record('mech_read_problem 竖排图：转正 + 对照图内联成第二张图 + 不混进 pieces（切块）',
    value.ok === true && Boolean(sheet) && sheetExists
      && mainInfo.size > 1000
      && Array.isArray(value.pieces) && value.pieces.length === 0
      && !value.pieces.includes(value.orient_sheet)
      && imageParts.length === 2,
    `orient_sheet=${value.orient_sheet ? '有' : '无'} pieces=${JSON.stringify(value.pieces)} `
      + `内容块图片=${imageParts.length} 张`)
} catch (error) {
  record('mech_read_problem 竖排图（方向候选对照图）', false, String(error?.message ?? error))
}

// ---- 6) mech_verify_reading：转写回渲染对照图 ----
try {
  const value = await run('mech_verify_reading', {
    workspace,
    original: join(HERE, 'cases', 'q1_beam.png'),
    transcription: '简支梁 L = 4 m，集中力 P = 10 kN 作用于跨中，求最大弯矩与支座反力。',
    out: join(TMP, 'compare.png'),
  })
  const info = await stat(value.path)
  record('mech_verify_reading 生成对照图并回报渲染器',
    value.ok === true && info.size > 1000 && typeof value.renderer === 'string',
    `${info.size} 字节 renderer=${value.renderer}`)
} catch (error) {
  record('mech_verify_reading', false, String(error?.message ?? error))
}

// ---- 7) mech_card：存 / 取 / 列 / 追加 ----
try {
  const saved = await run('mech_card', {
    workspace,
    action: 'save',
    card: {
      id: 'selftest-beam',
      title: '简支梁跨中集中力',
      source_images: ['cases/q1_beam.png'],
      transcription: 'L=4 m, P=10 kN',
      answers: [{ label: 'Mmax', value: 10, unit: 'kN*m' }],
    },
  })
  const appended = await run('mech_card', {
    workspace, action: 'append', id: 'selftest-beam', field: 'steps', items: ['求反力', '分段写内力方程'],
  })
  const got = await run('mech_card', { workspace, action: 'get', id: 'selftest-beam' })
  const listed = await run('mech_card', { workspace, action: 'list' })
  const ok = saved.ok && appended.ok && got.card.steps.length === 2
    && got.card.answers[0].value === 10 && listed.cards.some((card) => card.id === 'selftest-beam')
  record('mech_card 保存/追加/取回/列表', ok, `steps=${got.card.steps.length} 卡片数=${listed.cards.length}`)
} catch (error) {
  record('mech_card 保存/追加/取回/列表', false, String(error?.message ?? error))
}

// ---- 8) 错误路径：非法参数必须抛可读原因 ----
try {
  await run('mech_card', { workspace, action: 'append', id: 'selftest-beam' })
  record('mech_card 缺 field 时给出可读错误', false, '没有抛错')
} catch (error) {
  record('mech_card 缺 field 时给出可读错误', /field/.test(String(error?.message ?? '')), String(error?.message))
}

// ---- 9) viz_figure：通用引擎（学科无关；matplotlib 优先，缺则退回 Pillow） ----
try {
  const value = await run('viz_figure', {
    workspace,
    out: join(TMP, 'viz-selftest.png'),
    spec: {
      title: '自测：通用引擎',
      grid: { rows: 2, cols: 1 },
      axes: [
        {
          slot: [0, 0],
          title: '函数曲线',
          ylabel: 'f(x)',
          series: [
            { kind: 'function', expr: 'x**2*sin(x)', xrange: [-6, 6], label: 'x²sin x' },
            { kind: 'hline', y: 0 },
          ],
        },
        {
          slot: [1, 0],
          title: '图元与标注',
          xlabel: 'x',
          aspect: 'equal',
          xlim: [-2, 8],
          ylim: [-3, 5],
          series: [
            { kind: 'polygon', xy: [[0, 0], [6, 0], [6, 0.4], [0, 0.4]], facecolor: '#cfd8dc' },
            { kind: 'arrow', xy: [3, 0.4], xytext: [3, 3], color: '#c0392b' },
            { kind: 'annotate', xy: [3, 0.4], text: '跨中集中力', xytext: [60, -40] },
            { kind: 'text', xy: [1.5, -1.2], text: 'A' },
          ],
        },
      ],
      note: ['自测样例'],
    },
  })
  const blocks = assertLossless('viz_figure', '渲染内容块', registered.get('viz_figure').output.render({}, value))
  const hasImage = blocks.some((block) => block.type === 'image')
  record(
    `viz_figure 出图（后端 ${value.backend}，${value.bytes} 字节，${hasImage ? '含内联图片' : '未内联图片'}）`,
    value.ok === true && existsSync(value.path) && ['matplotlib', 'pillow'].includes(value.backend) && hasImage,
    value.unsupported?.length ? `后端降级项：${value.unsupported.join('；')}` : '',
  )
} catch (error) {
  record('viz_figure 出图', false, String(error?.message ?? error))
}

// ---- 10) viz_check：缺图时给出可读错误（不联网） ----
try {
  await run('viz_check', { workspace, image: 'definitely-missing.png' })
  record('viz_check 缺图时给出可读错误', false, '没有抛错')
} catch (error) {
  record('viz_check 缺图时给出可读错误', /不存在/.test(String(error?.message ?? '')), String(error?.message))
}

// ---- 11) solve_math：通用求解内核（符号优先，含独立复核） ----
try {
  const value = await run('solve_math', {
    workspace,
    spec: {
      op: 'solve',
      equations: ['U-12=0', 'U-R1*I-R2*I=0', 'R1-100=0', 'R2-200=0'],
      unknowns: ['U', 'I', 'R1', 'R2'],
    },
  })
  const solution = value.numeric?.[0] ?? {}
  record(
    `solve_math 欧姆定律方程组（I=${solution.I} A、U=${solution.U} V、全部复核通过）`,
    value.ok === true && approx(solution.I, 0.04) && approx(solution.U, 12)
      && (value.checks ?? []).length > 0 && value.checks.every((check) => check.pass),
    `exact=${JSON.stringify(value.exact?.[0])} checks=${(value.checks ?? []).length} 精确路径=${value.sympy !== false}`,
  )
} catch (error) {
  record('solve_math 欧姆定律方程组', false, String(error?.message ?? error))
}

// ---- 12) solve_math：必须如实报告降级，不许假装精确 ----
try {
  const value = await run('solve_math', { workspace, spec: { op: 'probe' } })
  const info = value.steps?.[0] ?? {}
  record(
    `solve_math probe 如实报告能力（精确路径=${info['精确路径']}）`,
    value.ok === true && typeof info['精确路径'] === 'boolean' && typeof info.sympy === 'string',
    `sympy=${info.sympy} numpy=${info.numpy} pylibs_exists=${info.pylibs_exists}`,
  )
  // steps 里是**对象**，直接 join 会打印成 [object Object]（真实踩到过）。
  // 面向模型的说明文字必须是可读的，这条断言挡住它再回来。
  const summary = String(value.summary ?? '')
  record(
    'solve_math 的说明文字可读（不出现 [object Object]）',
    summary.includes('过程：') && !summary.includes('[object Object]')
      && summary.includes(`sympy=${info.sympy}`),
    (summary.split('\n').find((line) => line.startsWith('过程：')) ?? '(没有"过程"这一行)').slice(0, 130),
  )
} catch (error) {
  record('solve_math probe', false, String(error?.message ?? error))
}

// ---- 13) viz_annotate：在图上做通用标注（不依赖学科） ----
try {
  const value = await run('viz_annotate', {
    workspace,
    src: join(HERE, 'cases', 'q1_beam.png'),
    out: join(TMP, 'annotated.png'),
    ops: [
      { op: 'box', box: [0.05, 0.05, 0.9, 0.3], color: '#c0392b', width: 3 },
      { op: 'text', x: 0.08, y: 0.12, text: '自测标注', size: 28, color: '#c0392b' },
    ],
  })
  const info = await stat(value.path)
  record('viz_annotate 标框+文字（PNG 落盘）',
    value.ok === true && info.size > 1000,
    `${info.size} 字节 ${value.width}x${value.height}`)
} catch (error) {
  record('viz_annotate 标框+文字', false, String(error?.message ?? error))
}

await rm(TMP, { recursive: true, force: true }).catch(() => {})

const failed = results.filter((item) => !item.ok)
console.log(`\n${results.length - failed.length}/${results.length} 通过`)
if (failed.length) {
  console.log('失败项：')
  for (const item of failed) console.log(`  - ${item.name}: ${item.detail}`)
  process.exitCode = 1
}