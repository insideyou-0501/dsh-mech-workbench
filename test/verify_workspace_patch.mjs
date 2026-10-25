// 验证「会话工作目录」修复的逻辑：从打补丁后的 index.js 里抽出
// sessionDirectory / workspaceOf 两个纯函数，在隔离沙箱里跑断言。
// 这一步不需要重启 DSH Desktop，用来确认补丁本身正确。

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import assert from 'node:assert/strict'

const targets = process.argv.slice(2)
if (targets.length === 0) {
  console.error('用法：node verify_workspace_patch.mjs <index.js> [...]')
  process.exit(2)
}

function extract(source) {
  const start = source.indexOf('function sessionDirectory')
  if (start < 0) throw new Error('找不到 sessionDirectory：补丁未生效')
  const end = source.indexOf('function workspaceOf', start)
  if (end < 0) throw new Error('找不到 workspaceOf')
  const tail = source.indexOf('\n}\n', end)
  if (tail < 0) throw new Error('workspaceOf 未闭合')
  return source.slice(start, tail + 3)
}

function load(source) {
  const body = `${extract(source)}\nreturn { sessionDirectory, workspaceOf }`
  return new Function('resolve', 'process', body)(resolve, process)
}

const SESSION = { id: 'test-session' }

function hostCtx(directory) {
  return { get: (name) => (name === 'workingDirectory' ? { get: () => directory } : undefined) }
}

let failed = 0
for (const target of targets) {
  const source = readFileSync(target, 'utf8')
  const { workspaceOf } = load(source)
  const cases = []

  // 1) 有会话：必须取到会话工作目录，而不是进程 cwd
  cases.push([
    '会话目录优先',
    () => workspaceOf({}, { agent: { session: SESSION } }, hostCtx('C:\\work\\ws')),
    resolve('C:\\work\\ws'),
  ])

  // 2) 显式 workspace 参数优先于会话目录
  cases.push([
    '显式 workspace 最优先',
    () => workspaceOf({ workspace: 'C:\\explicit' }, { agent: { session: SESSION } }, hostCtx('C:\\work\\ws')),
    resolve('C:\\explicit'),
  ])

  // 3) 没有 agent（独立自测）：退回进程 cwd，与旧行为一致
  cases.push(['无 agent 时退回进程 cwd', () => workspaceOf({}, {}, null), process.cwd()])

  // 4) 宿主没有该服务：不得抛错，退回进程 cwd
  cases.push([
    '宿主缺服务时安全降级',
    () => workspaceOf({}, { agent: { session: SESSION } }, { get: () => undefined }),
    process.cwd(),
  ])

  // 5) 服务取目录抛错：同样安全降级
  cases.push([
    '服务抛错时安全降级',
    () => {
      const ctx = {
        get: () => ({
          get: () => {
            throw new Error('boom')
          },
        }),
      }
      return workspaceOf({}, { agent: { session: SESSION } }, ctx)
    },
    process.cwd(),
  ])

  // 6) session 缺少 agent 字段也不能崩
  cases.push(['exec.agent 缺省不崩', () => workspaceOf({}, undefined, hostCtx('C:\\x')), process.cwd()])

  for (const [name, run, expected] of cases) {
    try {
      assert.equal(run(), expected)
      console.log(`  PASS  ${name}`)
    } catch (error) {
      failed += 1
      console.log(`  FAIL  ${name}：期望 ${expected}，实际 ${error.actual ?? error.message}`)
    }
  }

  console.log(`${target}：${cases.length - failed}/${cases.length} 通过`)
}

process.exit(failed === 0 ? 0 : 1)