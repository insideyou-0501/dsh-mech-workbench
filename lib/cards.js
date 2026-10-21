/**
 * 题目卡片：把一个题的「原图路径 / 转写 / 假设 / 步骤 / 答案 / 校核 / 存疑点」
 * 落盘成 JSON，供后续轮次（尤其是图片被压缩省略之后）按 id 取回。
 *
 * 目录默认 `<workspace>/.mech-cards`，每张卡片一个 `<id>.json`。
 *
 * @module dsh-mech-workbench/lib/cards
 */

import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises'
import { join, resolve } from 'node:path'

const DEFAULT_DIR = '.mech-cards'

/** 解析卡片目录：显式 `dir` 优先，否则 `<workspace>/.mech-cards`。 */
function cardsDir(options = {}) {
  if (options.dir) return resolve(options.dir)
  const workspace = options.workspace ? resolve(options.workspace) : process.cwd()
  return join(workspace, DEFAULT_DIR)
}

/** 把标题变成安全的文件名片段。 */
function slug(text, fallback = 'problem') {
  const cleaned = String(text ?? '')
    .trim()
    .replace(/[\\/:*?"<>|\s]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 48)
  return cleaned || fallback
}

function cardPath(dir, id) {
  return join(dir, `${slug(id)}.json`)
}

/**
 * 保存或合并一张卡片。
 *
 * @param payload - 卡片字段；`id` 决定文件名，`merge: true` 时与既有内容深合并。
 * @param options - `workspace` 或 `dir`。
 * @returns 写入路径与卡片内容。
 */
export async function saveCard(payload, options = {}) {
  const dir = cardsDir(options)
  await mkdir(dir, { recursive: true })
  const id = payload?.id || slug(payload?.title) || `problem-${Date.now()}`
  const path = cardPath(dir, id)
  let existing = {}
  if (payload?.merge !== false) {
    try {
      existing = JSON.parse(await readFile(path, 'utf8'))
    } catch {
      existing = {}
    }
  }
  const now = new Date().toISOString()
  const merged = {
    ...existing,
    ...payload,
    id,
    created: existing.created ?? now,
    updated: now,
  }
  delete merged.merge
  await writeFile(path, `${JSON.stringify(merged, null, 2)}\n`, 'utf8')
  return { path, card: merged }
}

/** 读取一张卡片。 */
export async function getCard(id, options = {}) {
  const path = cardPath(cardsDir(options), id)
  const raw = await readFile(path, 'utf8')
  return { path, card: JSON.parse(raw) }
}

/** 列出卡片目录下的全部卡片（按更新时间倒序）。 */
export async function listCards(options = {}) {
  const dir = cardsDir(options)
  let names = []
  try {
    names = (await readdir(dir)).filter((name) => name.endsWith('.json'))
  } catch {
    return { dir, cards: [] }
  }
  const cards = []
  for (const name of names) {
    try {
      const card = JSON.parse(await readFile(join(dir, name), 'utf8'))
      cards.push({
        id: card.id ?? name.replace(/\.json$/, ''),
        title: card.title ?? '',
        updated: card.updated ?? '',
        answers: card.answers ?? undefined,
      })
    } catch {
      cards.push({ id: name.replace(/\.json$/, ''), title: '(无法解析)' })
    }
  }
  cards.sort((a, b) => String(b.updated).localeCompare(String(a.updated)))
  return { dir, cards }
}

/**
 * 追加一段内容到卡片的某个数组字段（如 steps、doubts）。
 *
 * @param id - 卡片 id。
 * @param field - 数组字段名。
 * @param items - 追加条目。
 * @param options - `workspace` 或 `dir`。
 */
export async function appendCard(id, field, items, options = {}) {
  let existing = { id }
  try {
    existing = (await getCard(id, options)).card
  } catch {
    /* 卡片不存在则以最小骨架创建 */
  }
  const list = Array.isArray(existing[field]) ? [...existing[field]] : []
  list.push(...(Array.isArray(items) ? items : [items]))
  return saveCard({ ...existing, id, [field]: list }, options)
}