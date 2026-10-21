/**
 * 把生成的 PNG 变成模型能看到的内容块。
 *
 * 第一方 `read_image` 的做法是：在 execute 内先经 `ctx.attachments.saveImage`
 * 得到持久附件引用，再以 `{ type: 'image', attachment }` 内容块回传。这里沿用同一
 * 约定：execute 阶段只负责拿到引用（纯 JSON），render 阶段同步拼装内容块。
 *
 * 附件服务缺失、超限或被拒时降级为「仅路径 + 说明」，工具本身永不因此失败。
 *
 * @module dsh-mech-workbench/lib/images
 */

import { basename } from 'node:path'
import { readBytes } from './python.js'

/**
 * 把 PNG 存成持久附件引用。
 *
 * @param ctx - 工具上下文（用 `ctx.get('attachments')` 可选取服务）。
 * @param path - 已写好的 PNG 路径。
 * @returns `{ image, note }`：`image` 为可 JSON 序列化的附件引用，失败时只给 `note`。
 */
export async function saveImageRef(ctx, path) {
  let attachments
  try {
    attachments = typeof ctx?.get === 'function' ? ctx.get('attachments') : undefined
  } catch {
    attachments = undefined
  }
  if (!attachments) {
    return { note: '当前部署没有附件服务，图片未内联；请按路径查看该文件。' }
  }
  try {
    const data = await readBytes(path)
    const limits = attachments.imageLimits
    if (limits?.maxImageBytes && data.byteLength > limits.maxImageBytes) {
      return {
        note: `图片 ${data.byteLength} 字节超过部署单图上限 ${limits.maxImageBytes}，未内联；请按路径查看。`,
      }
    }
    const ref = await attachments.saveImage({ data, mediaType: 'image/png', name: basename(path) })
    // 字段与官方 referenceImageValue 保持一致：originalDimensions 必须再浅拷一层，
    // 否则万一它是类实例，整个返回值会被判「非纯对象」而失败。
    return {
      image: {
        attachmentId: ref.attachmentId,
        mediaType: ref.mediaType,
        bytes: ref.bytes,
        width: ref.width,
        height: ref.height,
        ...(ref.name === undefined ? {} : { name: ref.name }),
        ...(ref.originalDimensions === undefined
          ? {}
          : { originalDimensions: { ...ref.originalDimensions } }),
      },
    }
  } catch (error) {
    return { note: `图片未能附加到本次对话：${error?.message ?? error}。请按路径查看该文件。` }
  }
}

/**
 * 把任意返回值净化成 DSH 要求的「无损 JSON」。
 *
 * DSH 会用 `walkJsonValue` 校验工具返回值，以下一律判为**非法**，整次调用直接报
 * `tool "xxx" returned invalid output: value is not lossless JSON`：
 *   - `undefined`（对象里带这个键就算，`typeof undefined !== "object"`）
 *   - `NaN` / `±Infinity` / `-0`（`!Number.isFinite || Object.is(x, -0)`）
 *   - 类实例等非纯对象、稀疏数组、symbol 键
 *
 * 回传图片的工具最容易被 `undefined` 咬到：附件服务不可用时 `saveImageRef` 只给
 * `note`，而返回语句里照样写着 `image`，于是 `{ image: undefined }` 让**整个工具失败**
 * ——尽管 PNG 已经写好、说明文字也拼好了（真实踩到过，且 mock 自测抓不到，
 * 因为 mock 直接调 execute，不过 DSH 的校验）。
 *
 * @param value - 任意返回值。
 * @returns 可无损 JSON 往返的副本；对象里 `undefined` 的键被丢弃。
 */
export function toLossless(value) {
  return sanitize(value, new WeakSet())
}

/** 实际递归体；`seen` 防住环（有环时截断成 null 而不是无限递归爆栈）。 */
function sanitize(value, seen) {
  if (value === null || value === undefined) return null
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return null
    return Object.is(value, -0) ? 0 : value
  }
  if (typeof value === 'string' || typeof value === 'boolean') return value
  if (typeof value === 'bigint') return String(value)
  if (typeof value !== 'object') return null // function / symbol：退化成 null，不让整次调用失败
  if (seen.has(value)) return null // 环
  seen.add(value)
  try {
    if (Array.isArray(value)) return value.map((item) => sanitize(item, seen))
    const out = {}
    for (const key of Object.keys(value)) {
      const item = value[key]
      if (item === undefined) continue // 关键：不要留下 undefined 的键
      out[key] = sanitize(item, seen)
    }
    return out
  } finally {
    seen.delete(value)
  }
}

/**
 * 由工具结果值同步拼装内容块。
 *
 * @param value - 工具返回值（可含 `image` 与 `imageNote`；还可再带一张
 *   `extraImage` / `extraImageNote`，例如读题时的「方向候选对照图」——
 *   把两张图放进同一次工具结果，模型就不必再多花一轮去看第二张）。
 * @param text - 面向模型的说明文字。
 * @returns 内容块数组。
 */
export function contentFrom(value, text) {
  const parts = [{ type: 'text', text }]
  if (value?.imageNote) parts.push({ type: 'text', text: `（${value.imageNote}）` })
  if (value?.image) parts.push({ type: 'image', attachment: value.image })
  if (value?.extraImageNote) parts.push({ type: 'text', text: `（${value.extraImageNote}）` })
  if (value?.extraImage) parts.push({ type: 'image', attachment: value.extraImage })
  return parts
}