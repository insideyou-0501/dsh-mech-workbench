/**
 * 对**安装好的产物**做端到端验证（不是工作区源码）。
 *
 * 为什么必须有这个文件：包里的 `test/` 不随包发布，所以工作区自测只能证明
 * 源码是对的，证明不了「DSH 实际加载的那份代码」是对的。这里直接 import
 * 安装副本的 `index.js`，用可配置的附件服务跑真实工具，并按 DSH 的规则
 * （`walkJsonValue`）检查返回值能不能过「无损 JSON」这一关。
 *
 * 真实踩过的坑（本文件的由来）：`saveImageRef` 成功时只返回 `{ image }`，
 * 调用处却写 `imageNote: note`，`note` 是 `undefined`；DSH 看到 `undefined`
 * 值的键就判整次调用非法——
 *   `tool "mech_diagram" returned invalid output: value is not lossless JSON`
 * 于是**五个回传图片的工具全废，而 mech_analyze 正常**（它不回传图片）。
 * 工具其实成功了、PNG 也写好了，模型却只看到一条序列化错误。
 * 修复：注册处统一过 `toLossless`（见 `lib/images.js`）。
 *
 * 用法：
 *   node test/verify_installed.mjs                # 自动找当前 live 代次
 *   node test/verify_installed.mjs <安装副本目录>
 * 环境变量：VERIFY_WORKSPACE（输出目录）、VERIFY_ASSETS（测试素材目录）。
 */
import { existsSync, readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = dirname(HERE)

/** 按 desired.json 找当前 live 代次里的安装副本。 */
function discoverInstalled() {
  const generations = join(
    process.env.APPDATA ?? join(homedir(), 'AppData', 'Roaming'),
    'dsh-desktop', 'harness', 'profiles', '.generations',
  )
  let name
  try {
    const desired = JSON.parse(readFileSync(join(generations, 'desired.json'), 'utf8'))
    name = Array.isArray(desired) ? desired[0] : String(desired)
  } catch {
    return undefined
  }
  if (!name) return undefined
  return join(generations, 'live', name, 'node_modules', 'dsh-mech-workbench')
}

const pkg = process.argv[2] ?? discoverInstalled()
if (!pkg || !existsSync(pkg)) {
  console.error(
    `找不到安装副本：${pkg ?? '(未指定)'}\n`
      + '请先安装插件的 bundle，或把安装目录作为第一个参数传进来。',
  )
  process.exit(2)
}

// ---- 复刻 DSH walkJsonValue 的判定 ----
function losslessProblems(value, path = '$') {
  const problems = []
  const ancestors = new Set()
  const walk = (node, at) => {
    if (node === undefined) return void problems.push(`${at} 是 undefined`)
    if (node === null || typeof node === 'boolean' || typeof node === 'string') return
    if (typeof node === 'number') {
      if (!Number.isFinite(node)) problems.push(`${at} 非有限数字 ${node}`)
      else if (Object.is(node, -0)) problems.push(`${at} 是 -0`)
      return
    }
    if (typeof node !== 'object') return void problems.push(`${at} 类型 ${typeof node}`)
    if (ancestors.has(node)) return void problems.push(`${at} 有环`)
    ancestors.add(node)
    if (Array.isArray(node)) {
      if (Object.getPrototypeOf(node) !== Array.prototype) problems.push(`${at} 数组原型异常`)
      if (Reflect.ownKeys(node).length !== node.length + 1) problems.push(`${at} 稀疏数组`)
      for (let index = 0; index < node.length; index += 1) walk(node[index], `${at}[${index}]`)
    } else {
      const prototype = Object.getPrototypeOf(node)
      if (prototype !== null && prototype !== Object.prototype) {
        problems.push(`${at} 非纯对象（原型 ${prototype?.constructor?.name ?? 'null'}）`)
      }
      for (const key of Reflect.ownKeys(node)) {
        if (typeof key !== 'string') problems.push(`${at} 含 symbol 键`)
        else if (!Object.prototype.propertyIsEnumerable.call(node, key)) problems.push(`${at}.${key} 不可枚举`)
        else walk(node[key], `${at}.${key}`)
      }
    }
    ancestors.delete(node)
  }
  walk(value, path)
  return problems
}

class RealDimensions {
  constructor(width, height) {
    this.width = width
    this.height = height
  }
}

/** 模拟真实附件服务：完整引用形状（含**类实例** originalDimensions）。 */
const productionLike = {
  imageLimits: { maxImageBytes: 20 * 1024 * 1024 },
  calls: 0,
  async saveImage({ mediaType, name }) {
    productionLike.calls += 1
    return {
      attachmentId: 'sha256:installed',
      mediaType,
      width: 1400,
      height: 868,
      bytes: 4321,
      name,
      originalDimensions: new RealDimensions(2800, 1800),
    }
  },
}

/** 模拟"只给一部分字段"的服务实现：最容易产出 undefined 键。 */
const partial = {
  imageLimits: { maxImageBytes: 20 * 1024 * 1024 },
  async saveImage({ mediaType, name }) {
    return { attachmentId: `sha256:${name}`, mediaType, bytes: 4321 }
  },
}

let attachments = partial
const registered = new Map()
const ctx = {
  tools: { register: (definition) => registered.set(definition.name, definition) },
  get(key) {
    if (key === 'attachments') return attachments
    return undefined
  },
  logger: { warn() {}, info() {} },
}

const plugin = await import(pathToFileURL(join(pkg, 'index.js')).href)
await plugin.apply(ctx, {})

const results = []
const check = (name, ok, detail) => results.push({ name, ok: Boolean(ok), detail })
const workspace = process.env.VERIFY_WORKSPACE ?? join(HERE, 'tmp', 'verify-ws')
const assets = process.env.VERIFY_ASSETS ?? join(HERE, 'cases')
const sample = join(assets, 'q1_beam.png')

check('安装副本注册了 9 个工具', registered.size === 9, [...registered.keys()].join(', '))

const diagramArgs = {
  workspace,
  spec: {
    preset: 'axial',
    unit: 'kN',
    segments: [{ x0: 0, x1: 1, value: 20 }, { x0: 1, x1: 2, value: -10 }],
  },
}

for (const [label, service] of [['完整引用形状', productionLike], ['部分字段（易产 undefined）', partial]]) {
  attachments = service
  const before = service.calls ?? 0
  try {
    const value = await registered.get('mech_diagram').execute(diagramArgs, {})
    const problems = losslessProblems(value)
    const blocks = registered.get('mech_diagram').output.render({}, value)
    const blockProblems = losslessProblems(blocks)
    check(
      `mech_diagram（${label}）返回值与内容块都是无损 JSON`,
      problems.length === 0 && blockProblems.length === 0,
      problems.concat(blockProblems).slice(0, 4).join('；') || '干净',
    )
    check(
      `mech_diagram（${label}）不带 undefined 值的键`,
      !('imageNote' in value) && Object.values(value).every((item) => item !== undefined),
      `键：${Object.keys(value).join(', ')}`,
    )
    if (service === productionLike) {
      const image = value.image
      check(
        'mech_diagram（完整引用）图片内联且为纯对象',
        image?.attachmentId === 'sha256:installed'
          && Object.getPrototypeOf(image) === Object.prototype
          && Object.getPrototypeOf(image.originalDimensions) === Object.prototype
          && blocks.some((block) => block.type === 'image'),
        `attachmentId=${image?.attachmentId}，saveImage 调用 +${(service.calls ?? 0) - before}`,
      )
    }
  } catch (error) {
    check(`mech_diagram（${label}）`, false, `抛错：${String(error?.message ?? error)}`)
  }
}

// 其余回传图片的工具各走一遍（它们共用同一条图片回传路径）
attachments = productionLike
for (const name of ['mech_read_problem', 'viz_annotate', 'mech_verify_reading', 'viz_figure']) {
  const args = name === 'mech_read_problem'
    ? { workspace, image: sample, second_reader: false }
    : name === 'viz_annotate'
      ? { workspace, src: sample, ops: [{ op: 'box', box: [0.1, 0.1, 0.4, 0.4] }] }
      : name === 'mech_verify_reading'
        ? { workspace, original: sample, transcription: '验证用' }
        : { workspace, spec: { title: '验证', axes: [{ series: [{ kind: 'line', x: [0, 1], y: [0, 1] }] }] } }
  try {
    const value = await registered.get(name).execute(args, {})
    const problems = losslessProblems(value)
    const blocks = registered.get(name).output.render({}, value)
    const blockProblems = losslessProblems(blocks)
    check(
      `${name} 返回值与内容块都是无损 JSON`,
      problems.length === 0 && blockProblems.length === 0,
      problems.concat(blockProblems).slice(0, 3).join('；') || '干净',
    )
  } catch (error) {
    // 缺素材不算失败，但必须说清楚，不能悄悄跳过
    const message = String(error?.message ?? error)
    check(`${name}（素材缺失，已跳过）`, /不存在|not found|ENOENT|图片/.test(message), message.slice(0, 120))
  }
}

// ---- 竖排图 + 无 EXIF：方向候选对照图必须内联成第二张图，且不许混进 pieces ----
// 这一条**必须在安装产物上验**：工作区自测证明不了 DSH 实际加载的那份代码
// （`test/` 不随包发布，所以"源码对"和"装的这份对"是两件事）。
try {
  attachments = productionLike
  const value = await registered.get('mech_read_problem').execute(
    {
      workspace,
      image: join(assets, 'q5_text_rot90.png'),
      out_dir: join(workspace, 'images-rot'),
      second_reader: false,
      ops: [{ op: 'auto_orient' }],
    },
    {},
  )
  const problems = losslessProblems(value)
  const blocks = registered.get('mech_read_problem').output.render({}, value)
  const blockProblems = losslessProblems(blocks)
  const sheet = (value.outputs ?? []).find((item) => item.kind === 'orient-sheet')
  const imageBlocks = blocks.filter((block) => block.type === 'image')
  check(
    'mech_read_problem 竖排图（安装产物）：对照图内联成第二张图、不混进 pieces、返回值与内容块都无损',
    problems.length === 0 && blockProblems.length === 0
      && Boolean(value.orient_sheet) && Boolean(sheet)
      && Array.isArray(value.pieces) && value.pieces.length === 0
      && imageBlocks.length === 2,
    `orient_sheet=${value.orient_sheet ? '有' : '无'} pieces=${JSON.stringify(value.pieces)} `
      + `内容块图片=${imageBlocks.length} 张 `
      + (problems.concat(blockProblems).slice(0, 2).join('；') || '干净'),
  )
} catch (error) {
  check('mech_read_problem 竖排图（安装产物）', false, `抛错：${String(error?.message ?? error)}`)
}

const failed = results.filter((item) => !item.ok)
for (const item of results) {
  console.log(`${item.ok ? 'PASS' : 'FAIL'}  ${item.name}${item.detail ? `  — ${item.detail}` : ''}`)
}
console.log(`\n${results.length - failed.length}/${results.length} 通过`)
console.log(`安装副本：${pkg}`)
process.exit(failed.length ? 1 : 0)