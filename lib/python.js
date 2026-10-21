/**
 * Python 解释器解析与脚本调用。
 *
 * 依赖分三档，由 `requirement` 声明，脚本各取所需：
 *   * `base`       —— numpy + Pillow（随包的 Office 运行时就够，绘图兜底与图像处理用它）
 *   * `matplotlib` —— 再加 matplotlib（通用出图引擎的首选后端）
 *   * `sympy`      —— 再加 sympy（通用符号求解内核）
 * 也可以直接传模块名数组或逗号分隔的模块名，按需扩展而不必改这里。
 *
 * two 个刻意选择：
 *  1. spec 走临时文件而不是命令行，避免 Windows 命令行长度上限截断大图数据；
 *  2. 子进程的 stdout/stderr **写文件而不是管道** —— 受限沙箱里 Node 用管道 stdio
 *     捕获子进程输出会 `spawn EPERM`，文件通道在受限与非受限环境下都可用。
 *
 * @module dsh-mech-workbench/lib/python
 */

import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { mkdtemp, open, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { delimiter, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const SCRIPTS_DIR = fileURLToPath(new URL('../scripts', import.meta.url))

/** 插件根目录（安装后是 node_modules/dsh-mech-workbench）。 */
const PLUGIN_ROOT = fileURLToPath(new URL('..', import.meta.url))

/**
 * 随包 vendored 的**纯 Python** 依赖目录（sympy / mpmath）。
 * 由 `tools/vendor_pylibs.py` 生成：全程只是普通文件写入，绕开 pip 在受限沙箱里
 * 解包/改名被拒的问题，而且随插件包一起走，换机器不需要重新联网装。
 */
const PYLIBS = join(PLUGIN_ROOT, '.pylibs')

/** 内置 Office 运行时的默认位置（DSH Desktop 打包形态）。 */
const BUNDLED_PYTHON = join(
  'D:\\deepseek\\DSH Desktop\\resources\\office-runtime\\primary-runtime\\dependencies\\python',
  'python.exe',
)

/** 依赖档位 → 模块清单。 */
const REQUIREMENT_ALIASES = {
  base: ['numpy', 'PIL'],
  image: ['numpy', 'PIL'],
  matplotlib: ['numpy', 'PIL', 'matplotlib'],
  viz: ['numpy', 'PIL', 'matplotlib'],
  sympy: ['numpy', 'sympy'],
  symbolic: ['numpy', 'sympy'],
  math: ['numpy', 'sympy'],
  'sympy+matplotlib': ['numpy', 'sympy', 'matplotlib'],
  full: ['numpy', 'PIL', 'matplotlib', 'sympy'],
}

/**
 * 把 `requirement` 归一化成模块清单。
 *
 * @param requirement - 档位名、模块名数组，或逗号分隔的模块名。
 * @returns 模块名数组（非空）。
 */
export function requiredModules(requirement = 'base') {
  if (Array.isArray(requirement)) {
    const list = requirement.map((name) => String(name).trim()).filter(Boolean)
    return list.length ? list : REQUIREMENT_ALIASES.base
  }
  const key = String(requirement ?? 'base').trim()
  if (REQUIREMENT_ALIASES[key]) return REQUIREMENT_ALIASES[key]
  const list = key.split(',').map((name) => name.trim()).filter(Boolean)
  return list.length ? list : REQUIREMENT_ALIASES.base
}

/** 候选解释器，按优先级排列。 */
function pythonCandidates() {
  const list = []
  if (process.env.DSH_VIZ_PYTHON) list.push(process.env.DSH_VIZ_PYTHON)
  if (process.env.DSH_SYMPY_PYTHON) list.push(process.env.DSH_SYMPY_PYTHON)
  if (process.env.DSH_MECH_PYTHON) list.push(process.env.DSH_MECH_PYTHON)
  const resources = process.env.DSH_DESKTOP_OFFICE_RESOURCES
  if (resources) list.push(join(resources, 'primary-runtime', 'dependencies', 'python', 'python.exe'))
  list.push(BUNDLED_PYTHON)
  list.push(process.platform === 'win32' ? 'py' : 'python3', 'python')
  return list
}

/** 每种需求各自缓存一次解析结果。 */
const cached = new Map()

/** 需求对应的探测代码：把随包 .pylibs 加进 sys.path 后再逐个 import。 */
function probeCode(modules) {
  return [
    'import sys',
    `sys.path.insert(0, ${JSON.stringify(PYLIBS)})`,
    'import importlib',
    `missing = []`,
    `for name in ${JSON.stringify(modules)}:`,
    '    try:',
    '        importlib.import_module(name)',
    '    except Exception as exc:',
    "        missing.append('%s:%s' % (name, type(exc).__name__))",
    "print('ok' if not missing else 'missing ' + ' '.join(missing))",
  ].join('\n')
}

/** 子进程环境：把 .pylibs 放进 PYTHONPATH，任何脚本都能直接 import sympy。 */
function childEnv(extra) {
  const parts = [PYLIBS]
  if (process.env.PYTHONPATH) parts.push(process.env.PYTHONPATH)
  return {
    ...process.env,
    PYTHONPATH: parts.join(delimiter),
    // stdout 被重定向到文件时 Python 默认用系统区域编码（这里是 GBK），
    // 会让返回的路径/中文变成乱码。强制 UTF-8，脚本侧无需各自处理。
    PYTHONIOENCODING: 'utf-8',
    PYTHONUTF8: '1',
    ...extra,
  }
}

/**
 * 运行一个进程，把 stdout/stderr 落到文件再读回来。
 *
 * @param command - 可执行文件（或 PATH 上的命令名）。
 * @param args - 参数数组（不经 shell）。
 * @param options - `dir` 临时目录、`timeoutMs` 上限。
 * @returns `{ code, stdout, stderr, timedOut, spawnError }`。
 */
async function runProcess(command, args, options) {
  const dir = options.dir ?? (await mkdtemp(join(tmpdir(), 'dsh-mech-')))
  const stamp = `${Date.now()}-${Math.random().toString(36).slice(2)}`
  const outPath = join(dir, `out-${stamp}.log`)
  const errPath = join(dir, `err-${stamp}.log`)
  const outHandle = await open(outPath, 'w')
  const errHandle = await open(errPath, 'w')
  let child
  try {
    child = spawn(command, args, {
      stdio: ['ignore', outHandle.fd, errHandle.fd],
      windowsHide: true,
      env: childEnv(options.env),
    })
  } catch (error) {
    await outHandle.close().catch(() => {})
    await errHandle.close().catch(() => {})
    return { code: null, stdout: '', stderr: '', timedOut: false, spawnError: error }
  }
  const outcome = await new Promise((resolve) => {
    let settled = false
    const finish = (value) => {
      if (!settled) {
        settled = true
        resolve(value)
      }
    }
    const timer = setTimeout(() => {
      try {
        child.kill()
      } catch {
        /* 进程可能已退出 */
      }
      finish({ code: null, timedOut: true, spawnError: undefined })
    }, options.timeoutMs ?? 180000)
    child.once('error', (error) => {
      clearTimeout(timer)
      finish({ code: null, timedOut: false, spawnError: error })
    })
    child.once('close', (code) => {
      clearTimeout(timer)
      finish({ code, timedOut: false, spawnError: undefined })
    })
  })
  await outHandle.close().catch(() => {})
  await errHandle.close().catch(() => {})
  const stdout = await readFile(outPath, 'utf8').catch(() => '')
  const stderr = await readFile(errPath, 'utf8').catch(() => '')
  await rm(outPath, { force: true }).catch(() => {})
  await rm(errPath, { force: true }).catch(() => {})
  return { ...outcome, stdout, stderr }
}

/**
 * 解析一个满足依赖要求的 Python 解释器。
 *
 * @param requirement - `'base'` / `'matplotlib'` / `'sympy'` 等档位名，
 *   或模块名数组、逗号分隔的模块名。探测时会把随包 `.pylibs` 加进 `sys.path`。
 * @returns 解释器可执行文件路径（或 PATH 上的命令名）。
 * @throws 全部候选都不可用时给出可执行的修复建议。
 */
export async function resolvePython(requirement = 'base') {
  const modules = requiredModules(requirement)
  const key = modules.join(',')
  if (cached.has(key)) return cached.get(key)
  const problems = []
  const dir = await mkdtemp(join(tmpdir(), 'dsh-mech-probe-'))
  try {
    for (const candidate of pythonCandidates()) {
      if ((candidate.includes('\\') || candidate.includes('/')) && !existsSync(candidate)) {
        problems.push(`${candidate}: 不存在`)
        continue
      }
      const probe = await runProcess(candidate, ['-c', probeCode(modules)], {
        dir,
        timeoutMs: 60000,
      })
      if (probe.spawnError) {
        problems.push(`${candidate}: ${probe.spawnError.message ?? probe.spawnError}`)
      } else if (probe.timedOut) {
        problems.push(`${candidate}: 探测超时`)
      } else if (probe.code === 0 && probe.stdout.includes('ok')) {
        cached.set(key, candidate)
        return candidate
      } else {
        const detail = String(probe.stdout).trim() || `退出码 ${probe.code}`
        problems.push(`${candidate}: 缺少 ${modules.join(' + ')}（${detail.slice(0, 200)}）`)
      }
    }
  } finally {
    await rm(dir, { recursive: true, force: true }).catch(() => {})
  }
  throw new Error(
    `找不到可用的 Python 解释器（需要 ${modules.join(' + ')}）。` +
      `已尝试：${problems.join('；')}。` +
      '可用 DSH_MECH_PYTHON 指定解释器、DSH_VIZ_PYTHON 指定带 matplotlib 的解释器、' +
      'DSH_SYMPY_PYTHON 指定带 sympy 的解释器；' +
      '纯 Python 依赖也可用 `python tools/vendor_pylibs.py --list <包名>` 装进随包的 .pylibs。',
  )
}

/**
 * 运行一个 Python 脚本并取回它打印的 JSON。
 *
 * @param script - `scripts/` 下的脚本文件名。
 * @param spec - 传给脚本的 JSON spec。
 * @param options - `timeoutMs` 上限、`python` 显式解释器、`requirement` 依赖要求。
 * @returns 脚本输出的 JSON 对象。
 * @throws 脚本失败、输出非 JSON 或 `ok: false` 时抛出带原因的 Error。
 */
export async function runScript(script, spec, options = {}) {
  const timeoutMs = options.timeoutMs ?? 180000
  const python = options.python ?? (await resolvePython(options.requirement ?? 'base'))
  const dir = await mkdtemp(join(tmpdir(), 'dsh-mech-'))
  const specPath = join(dir, 'spec.json')
  try {
    await writeFile(specPath, JSON.stringify(spec), 'utf8')
    const run = await runProcess(python, [join(SCRIPTS_DIR, script), '--spec', specPath], {
      dir,
      timeoutMs,
    })
    if (run.spawnError) throw new Error(`无法启动 ${script}：${run.spawnError.message ?? run.spawnError}`)
    if (run.timedOut) throw new Error(`${script} 执行超过 ${Math.round(timeoutMs / 1000)} 秒，已终止`)
    const parsed = parseJsonObject(run.stdout)
    if (!parsed) {
      throw new Error(
        `${script} 没有输出可解析的 JSON（退出码 ${run.code}）。stderr：` +
          `${String(run.stderr).trim().slice(0, 800) || '(空)'}`,
      )
    }
    if (parsed.ok === false) throw new Error(parsed.error ?? `${script} 报告失败`)
    return parsed
  } finally {
    await rm(dir, { recursive: true, force: true }).catch(() => {})
  }
}

/** 从混合输出里取出最后一个 JSON 对象。 */
function parseJsonObject(text) {
  if (!text) return undefined
  const trimmed = String(text).trim()
  try {
    return JSON.parse(trimmed)
  } catch {
    /* 继续按括号配对扫描 */
  }
  const start = trimmed.indexOf('{')
  if (start < 0) return undefined
  let depth = 0
  let inString = false
  let escaped = false
  for (let i = start; i < trimmed.length; i += 1) {
    const ch = trimmed[i]
    if (inString) {
      if (escaped) escaped = false
      else if (ch === '\\') escaped = true
      else if (ch === '"') inString = false
      continue
    }
    if (ch === '"') inString = true
    else if (ch === '{') depth += 1
    else if (ch === '}') {
      depth -= 1
      if (depth === 0) {
        try {
          return JSON.parse(trimmed.slice(start, i + 1))
        } catch {
          return undefined
        }
      }
    }
  }
  return undefined
}

/** 读取图片字节，供附件服务使用。 */
export async function readBytes(path) {
  return new Uint8Array(await readFile(path))
}