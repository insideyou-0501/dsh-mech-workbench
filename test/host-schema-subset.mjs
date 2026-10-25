/**
 * 宿主 JSON Schema 子集的本地校验器。
 *
 * 为什么自测需要它：DSH 把 `ctx.tools.register` 收到的 `definition.parameters`
 * 与 `definition.output.schema` 当作**原始 JSON Schema 子集**校验。而本插件的
 * `index.js` 用 `defineTool` 把作者 DSL（参数值是 `{type:'string'}` 这样的值描述，
 * 并允许宿主专用的 `type:'json'`）编译成那个子集。一旦 `@deepseek-ai/dsh-tools`
 * 解析不到、`defineTool` 退化成恒等函数，作者 DSL 就会原样进注册表并被宿主拒绝，
 * 插件静默不激活（见 issue #3）。
 *
 * 原来的 mock registry 只把 definition 塞进 Map、什么都不校验，于是这条路径在离线
 * 自测里永远是绿的。这里按宿主规则复核，让同类问题在自测阶段就变红。
 *
 * 镜像自 `dsh-tools/lib/types/json-schema.js` 的 `assertSupportedJsonSchema`，
 * 规则要点：
 *  - `type` 只能是 object/array/string/number/integer/boolean/null；
 *    作者专用的 `json` 节点必须被编译成「仅注解」的空节点，绝不能留到注册表；
 *  - 除 type/oneOf/properties/required/additionalProperties/items/enum/const
 *    （约束）与 description/title/default/examples（注解）之外的关键字一律拒绝；
 *  - `object` 上若出现 `additionalProperties` 必须是 boolean；
 *  - `required` 必须是字符串数组，且每项都出现在 `properties` 里；
 *  - `enum`/`const` 只能用于标量类型，且值类型要与声明的 type 一致；
 *  - 既无 type 也无 oneOf 的节点视为「不加约束的 JSON」，但不得再带约束关键字。
 */

const CONSTRAINT_KEYWORDS = new Set([
  'type',
  'oneOf',
  'properties',
  'required',
  'additionalProperties',
  'items',
  'enum',
  'const',
])
const ANNOTATION_KEYWORDS = new Set(['description', 'title', 'default', 'examples'])
const SCHEMA_TYPES = ['object', 'array', 'string', 'number', 'integer', 'boolean', 'null']
const SCALAR_TYPES = ['string', 'number', 'integer', 'boolean', 'null']

function isPlainRecord(value) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return false
  const proto = Object.getPrototypeOf(value)
  return proto === Object.prototype || proto === null
}

function isPlainArray(value) {
  if (!Array.isArray(value)) return false
  if (Object.keys(value).length !== value.length) return false
  for (let index = 0; index < value.length; index += 1) {
    if (!Object.hasOwn(value, index)) return false
  }
  return true
}

function isJsonNumber(value) {
  return typeof value === 'number' && Number.isFinite(value) && !Object.is(value, -0)
}

function isJsonValue(value, seen = new Set()) {
  if (value === null) return true
  const kind = typeof value
  if (kind === 'boolean' || kind === 'string') return true
  if (kind === 'number') return isJsonNumber(value)
  if (kind !== 'object') return false
  if (seen.has(value)) return false
  seen.add(value)
  let ok = true
  if (Array.isArray(value)) {
    for (let index = 0; index < value.length && ok; index += 1) {
      if (!(index in value) || !isJsonValue(value[index], seen)) ok = false
    }
  } else if (!isPlainRecord(value)) {
    ok = false
  } else {
    for (const key of Object.keys(value)) {
      if (!isJsonValue(value[key], seen)) {
        ok = false
        break
      }
    }
  }
  seen.delete(value)
  return ok
}

function scalarMatches(type, value) {
  switch (type) {
    case 'string': return typeof value === 'string'
    case 'number': return isJsonNumber(value)
    case 'integer': return isJsonNumber(value) && Number.isInteger(value)
    case 'boolean': return typeof value === 'boolean'
    case 'null': return value === null
    default: return false
  }
}

/** 收集一棵原始 schema 树的全部违规（显式栈，避免深递归）。 */
function collectViolations(root, rootPath, violations) {
  const seen = new Set()
  const tasks = [{ kind: 'enter', node: root, path: rootPath }]
  for (let task = tasks.pop(); task !== undefined; task = tasks.pop()) {
    if (task.kind === 'leave') {
      seen.delete(task.node)
      continue
    }
    if (task.kind === 'object-tail') {
      const { node, path, properties } = task
      if (Object.hasOwn(node, 'required')) {
        const required = node.required
        if (!isPlainArray(required) || required.some((entry) => typeof entry !== 'string')) {
          violations.push(`${path}.required must be an array of strings`)
        } else if (isPlainRecord(properties)) {
          for (const key of required) {
            if (!Object.hasOwn(properties, key)) {
              violations.push(`${path}.required names "${key}" which is not in properties`)
            }
          }
        }
      }
      if (Object.hasOwn(node, 'additionalProperties') && typeof node.additionalProperties !== 'boolean') {
        violations.push(`${path}.additionalProperties must be a boolean`)
      }
      continue
    }

    const { node, path } = task
    if (!isPlainRecord(node)) {
      violations.push(`${path} must be a schema object`)
      continue
    }
    if (seen.has(node)) {
      violations.push(`${path} is circular`)
      continue
    }
    seen.add(node)
    tasks.push({ kind: 'leave', node })

    for (const key of Object.keys(node)) {
      if (CONSTRAINT_KEYWORDS.has(key)) continue
      if (ANNOTATION_KEYWORDS.has(key)) {
        if (!isJsonValue(node[key])) violations.push(`${path}.${key} annotation must be lossless JSON data`)
        continue
      }
      violations.push(
        `${path}.${key} is not a supported keyword (subset: type/oneOf/properties/required/additionalProperties/items/enum/const + annotations)`,
      )
    }

    const hasType = Object.hasOwn(node, 'type')
    const hasOneOf = Object.hasOwn(node, 'oneOf')
    if (hasType && hasOneOf) {
      violations.push(`${path} cannot declare both type and oneOf`)
      continue
    }
    if (!hasOneOf) {
      if (!hasType) {
        // 仅注解节点 = 不加约束的 JSON。作者 DSL 的 `type:'json'` 编译后就是这种形状。
        for (const key of ['properties', 'required', 'additionalProperties', 'items', 'enum', 'const']) {
          if (Object.hasOwn(node, key)) violations.push(`${path}.${key} requires type or oneOf`)
        }
        continue
      }
      const type = node.type
      if (typeof type !== 'string' || !SCHEMA_TYPES.includes(type)) {
        violations.push(
          Array.isArray(type)
            ? `${path}.type must be a single type string (type arrays are not supported)`
            : `${path}.type must be one of ${SCHEMA_TYPES.join('/')}`,
        )
        continue
      }

      const allowedFor = {
        properties: ['object'],
        required: ['object'],
        additionalProperties: ['object'],
        items: ['array'],
        enum: SCALAR_TYPES,
        const: SCALAR_TYPES,
      }
      for (const [key, types] of Object.entries(allowedFor)) {
        if (Object.hasOwn(node, key) && !types.includes(type)) {
          violations.push(`${path}.${key} is not supported on type "${type}"`)
        }
      }

      if (type === 'object') {
        const properties = Object.hasOwn(node, 'properties') ? node.properties : undefined
        tasks.push({ kind: 'object-tail', node, path, properties })
        if (Object.hasOwn(node, 'properties')) {
          if (!isPlainRecord(properties)) {
            violations.push(`${path}.properties must be an object of schemas`)
          } else {
            const entries = Object.entries(properties)
            for (let index = entries.length - 1; index >= 0; index -= 1) {
              tasks.push({ kind: 'enter', node: entries[index][1], path: `${path}.properties.${entries[index][0]}` })
            }
          }
        }
      } else if (type === 'array') {
        if (Object.hasOwn(node, 'items')) tasks.push({ kind: 'enter', node: node.items, path: `${path}.items` })
      } else {
        const hasEnum = Object.hasOwn(node, 'enum')
        const declaredEnum = hasEnum ? node.enum : undefined
        const enumValid =
          isPlainArray(declaredEnum) &&
          declaredEnum.length > 0 &&
          declaredEnum.every((entry) => scalarMatches(type, entry))
        if (hasEnum && !enumValid) violations.push(`${path}.enum must be a non-empty array of ${type} values`)
        if (Object.hasOwn(node, 'const') && !scalarMatches(type, node.const)) {
          violations.push(`${path}.const must be a ${type} value`)
        }
      }
      continue
    }

    // hasOneOf
    const oneOf = node.oneOf
    for (const key of ['properties', 'required', 'additionalProperties', 'items', 'enum', 'const']) {
      if (Object.hasOwn(node, key)) violations.push(`${path}.${key} is not supported beside oneOf`)
    }
    if (!isPlainArray(oneOf) || oneOf.length < 2) {
      violations.push(`${path}.oneOf must be an array of at least two schemas`)
    } else {
      for (let index = oneOf.length - 1; index >= 0; index -= 1) {
        tasks.push({ kind: 'enter', node: oneOf[index], path: `${path}.oneOf[${index}]` })
      }
    }
  }
}

/**
 * 断言一份原始 schema 落在宿主支持的子集内；违规时抛出带路径的错误。
 * @param {unknown} schema 待校验的 schema（对应 `definition.parameters` 或 `output.schema`）
 * @param {string} [label] 出错时显示的来源名
 */
export function assertHostSchemaSubset(schema, label = 'schema') {
  const violations = []
  collectViolations(schema, label, violations)
  if (violations.length > 0) {
    const error = new Error(`unsupported JSON schema: ${violations.join('; ')}`)
    error.violations = violations
    throw error
  }
}
