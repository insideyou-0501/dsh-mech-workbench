#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vision_cases.py —— 「第二读者（视觉转写）」环节的离线自测：不联网、不打任何真实端点。

锁的是**线上真故障**（不是假想的边界）：

  症状：`mech_read_problem` 的第二读者明明**看到了一份完整可用的转写**，上层却只拿到
        `parsed=false` 与空对象——那一次独立的复核等于白做，而模型还以为"视觉引擎没读出东西"。
  根因（两条叠加）：
    1) 活跃链上的 provider 原来 `jsonObject = False`，即**不发** `response_format`，
       模型于是爱回自然语言；而 deepseek 恰好在链尾。
    2) `extract_json` 失败后**直接 continue**，最后一轮失败就掉进兜底分支，把那份原文丢了。
  修复：json_object 一律开（不认的端点自动退回纯提示词约束）；解析失败先在本 provider 上
        做一次**纯文本**的「整理成 JSON」重试；实在不行也必须把原文放进 `raw`。

为什么离线也能测：`vision.py` 有 `__main__` 保护，可以安全 import。这里把
`call_provider` / `load_key` / `urllib.request.urlopen` 换成本地假件——
**上传的图片内容不参与判断**，被测的是"发什么请求、怎么解析、失败了怎么回报"这些契约。

用法：
    <python> vision_cases.py

输出：逐项 PASS/FAIL → 末尾 "N/M passed"。
"""

import contextlib
import importlib.util
import io
import json
import os
import sys
import urllib.error

sys.dont_write_bytecode = True

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
SCRIPTS = os.path.join(PLUGIN, "scripts")
CASES = os.path.join(HERE, "cases")
VISION = os.path.join(SCRIPTS, "vision.py")
CASE_IMAGE = os.path.join(CASES, "q1_beam.png")

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append({"name": name, "ok": bool(ok), "detail": str(detail)})
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  — " + str(detail)) if detail else ""))


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# 假件：把"网络"和"凭据库"都换成本地可控的东西
# --------------------------------------------------------------------------- #
class FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code=400, body=b'{"error":"response_format is not supported"}'):
    return urllib.error.HTTPError("https://example.invalid/v1/chat/completions", code,
                                  "Bad Request", {}, io.BytesIO(body))


def patch_urlopen(module, behaviour):
    """替换 urllib.request.urlopen；behaviour(index, body) 返回 payload dict 或抛异常。"""
    captured = []
    original = module.urllib.request.urlopen

    def fake_urlopen(request, timeout=None):
        body = json.loads(request.data.decode("utf-8"))
        captured.append(body)
        outcome = behaviour(len(captured) - 1, body)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)

    module.urllib.request.urlopen = fake_urlopen
    return captured, lambda: setattr(module.urllib.request, "urlopen", original)


def patch_call_provider(module, responder):
    """替换 call_provider；responder(index, provider, images, prompt) 返回文本或抛异常。"""
    calls = []
    original = module.call_provider

    def fake(provider, images_b64, prompt, timeout):
        calls.append({"provider": provider["name"], "images": list(images_b64), "prompt": prompt})
        return responder(len(calls) - 1, provider, images_b64, prompt)

    module.call_provider = fake
    return calls, lambda: setattr(module, "call_provider", original)


def patch_load_key(module, allowed):
    """只让 allowlist 里的环境变量"有 key"，其余一律没有。

    这样链上会尝试哪些 provider 完全由用例决定，**不读真实的凭据库**——
    否则本机的 .credentials.yaml 会让"链的顺序"随环境漂移。
    """
    original = module.load_key

    def fake(name):
        if name in allowed:
            return "selftest-key-%s" % name, "selftest"
        return None, None

    module.load_key = fake
    return lambda: setattr(module, "load_key", original)


def spec_for(**extra):
    spec = {"images": [CASE_IMAGE], "prompt": "把这张题图转写成结构化 JSON。"}
    spec.update(extra)
    return spec


def run_quiet(module, spec):
    """`vision.run` 每步都往 stderr 打日志；run_all 是 `stdout + stderr` 取末尾 3 行，
    不静音的话汇总行会被日志顶掉（末尾 3 行就看不到 "N/M passed" 了）。"""
    with contextlib.redirect_stderr(io.StringIO()):
        return module.run(spec)


NATURAL_TEXT = ("这是一张简支梁的题图：跨中作用 P = 10 kN，梁长 L = 4 m，"
                "抗弯截面系数 W = 120 cm3，图中有 A、B 两个支座。")
STRUCTURED = {"subject": "材料力学", "given": ["P = 10 kN", "L = 4 m"],
              "low_confidence": []}
STRUCTURED_TEXT = json.dumps(STRUCTURED, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# 用例
# --------------------------------------------------------------------------- #
def main():
    if not os.path.isfile(VISION):
        print("找不到 %s" % VISION)
        return 1
    if not os.path.isfile(CASE_IMAGE):
        print("缺少测试图 %s（先跑 test/cases/make_cases.py）" % CASE_IMAGE)
        return 1

    module = load_module(VISION, "vision_under_test")
    print("=" * 78)
    print("mech-workbench 「第二读者」离线自测（不联网、不读真实凭据库）")
    print("被测：%s" % VISION)
    print("=" * 78)

    # 1) provider 表：jsonObject 必须**显式**声明，不许靠默认值悄悄不发
    providers = module.build_providers()
    changed = {item["name"]: item.get("jsonObject") for item in providers
               if item["name"] in ("siliconflow-qwen", "deepseek")}
    record("provider 表：jsonObject 全部显式声明，且曾经漏开的两家（siliconflow-qwen / deepseek）已开",
           all("jsonObject" in item for item in providers)
           and changed.get("siliconflow-qwen") is True
           and changed.get("deepseek") is True,
           "providers=%s" % {item["name"]: item.get("jsonObject") for item in providers})

    # 2) jsonObject=True → 真的发 response_format
    provider_on = dict(providers[0], name="fake-on", jsonObject=True, maxTokens=64)
    captured, restore = patch_urlopen(
        module, lambda index, body: {"choices": [{"message": {"content": STRUCTURED_TEXT}}]})
    try:
        text = module.call_provider(provider_on, [], "转写", 5)
    finally:
        restore()
    record("call_provider：jsonObject=True 时请求体里带 response_format=json_object",
           bool(captured) and captured[0].get("response_format") == {"type": "json_object"}
           and text == STRUCTURED_TEXT,
           "response_format=%s" % (captured[0].get("response_format") if captured else "（没有请求）"))

    # 3) jsonObject=False → 不发（显式的例外不能被误当成默认行为）
    provider_off = dict(provider_on, jsonObject=False)
    captured, restore = patch_urlopen(
        module, lambda index, body: {"choices": [{"message": {"content": STRUCTURED_TEXT}}]})
    try:
        module.call_provider(provider_off, [], "转写", 5)
    finally:
        restore()
    record("call_provider：jsonObject=False 时不发 response_format（例外必须显式）",
           bool(captured) and "response_format" not in captured[0],
           "请求体键=%s" % (sorted(captured[0].keys()) if captured else "（没有请求）"))

    # 4) 端点 400 拒绝 response_format → 自动退回纯提示词约束再试一次（保护不能被删）
    def reject_then_accept(index, body):
        if index == 0:
            return http_error(400)
        return {"choices": [{"message": {"content": STRUCTURED_TEXT}}]}

    captured, restore = patch_urlopen(module, reject_then_accept)
    try:
        text = module.call_provider(provider_on, [], "转写", 5)
        raised = None
    except Exception as exc:                                          # pragma: no cover
        text, raised = None, exc
    finally:
        restore()
    record("call_provider：端点 400 拒绝 response_format 时退回纯提示词约束并成功",
           raised is None and text == STRUCTURED_TEXT and len(captured) == 2
           and "response_format" in captured[0] and "response_format" not in captured[1],
           "调用 %d 次，第二次带 response_format=%s"
           % (len(captured), ("response_format" in captured[1]) if len(captured) > 1 else "?"))

    # 5) + 6) 首次自然语言 → 纯文本「整理成 JSON」重试成功，且原文与整理结果都保留
    calls, restore_call = patch_call_provider(
        module,
        lambda index, provider, images, prompt: NATURAL_TEXT if index == 0 else STRUCTURED_TEXT)
    restore_key = patch_load_key(module, {"DEEPSEEK_API_KEY"})
    try:
        result = run_quiet(module, spec_for(provider="deepseek"))
        error = None
    except Exception as exc:
        result, error = None, exc
    finally:
        restore_call()
        restore_key()
    record("视觉引擎首次回自然语言 → 整理重试成功：parsed/repaired 都为真，原文与整理结果都留着",
           error is None and result and result.get("parsed") is True
           and result.get("repaired") is True
           and result.get("raw") == NATURAL_TEXT
           and result.get("repair_raw") == STRUCTURED_TEXT
           and (result.get("attempts") or [{}])[0].get("status") == "ok_after_repair",
           "provider=%s parsed=%s repaired=%s" % (result.get("provider"), result.get("parsed"),
                                                  result.get("repaired"))
           if result else str(error))
    repair_prompt = calls[1]["prompt"] if len(calls) > 1 else ""
    record("整理重试是纯文本调用（不重复上传图片），且提示词明确禁止新增/猜测内容",
           len(calls) == 2 and calls[1]["images"] == []
           and "不要新增、不要猜测" in repair_prompt
           and NATURAL_TEXT in repair_prompt,
           "重试调用带图 %d 张" % (len(calls[1]["images"]) if len(calls) > 1 else -1))

    # 7) 整理重试也失败 → 降级但**不是**失败：原文一字不改地保留
    def natural_then_boom(index, provider, images, prompt):
        if index == 0:
            return NATURAL_TEXT
        raise RuntimeError("模拟重试调用失败")

    calls, restore_call = patch_call_provider(module, natural_then_boom)
    restore_key = patch_load_key(module, {"DEEPSEEK_API_KEY"})
    try:
        result = run_quiet(module, spec_for(provider="deepseek"))
        error = None
    except Exception as exc:
        result, error = None, exc
    finally:
        restore_call()
        restore_key()
    record("整理重试也拿不到 JSON → 降级为原文（degraded 标记 + raw 原文一字不改，绝不丢转写）",
           error is None and result and result.get("ok") is True
           and result.get("parsed") is False and result.get("degraded") is True
           and result.get("raw") == NATURAL_TEXT
           and result.get("transcription") is None
           and "raw" in str(result.get("warning") or "")
           and (result.get("attempts") or [{}])[0].get("status") == "bad_json",
           "parsed=%s degraded=%s raw 长度=%s" % (result.get("parsed"), result.get("degraded"),
                                                  len(result.get("raw") or ""))
           if result else str(error))

    # 8) 首次就是合法 JSON → 不做多余重试
    calls, restore_call = patch_call_provider(
        module, lambda index, provider, images, prompt: STRUCTURED_TEXT)
    restore_key = patch_load_key(module, {"DEEPSEEK_API_KEY"})
    try:
        result = run_quiet(module, spec_for(provider="deepseek"))
        error = None
    except Exception as exc:
        result, error = None, exc
    finally:
        restore_call()
        restore_key()
    record("视觉引擎首次就回合法 JSON → parsed=True、无 repaired 标记、只调用一次",
           error is None and result and result.get("parsed") is True
           and not result.get("repaired") and len(calls) == 1
           and result.get("transcription") == STRUCTURED,
           "调用 %d 次，keys=%s" % (len(calls), sorted(result.keys()) if result else "?"))

    # 9) 链路前一家失败、后一家救回来：attempts 必须如实记录（index.js 用它报"第二读者链路"）
    os.environ["VISION_FALLBACKS"] = json.dumps([{
        "name": "selftest-fallback", "baseUrl": "https://fallback.invalid/v1",
        "model": "selftest-vl", "apiKeyEnv": "OPENAI_API_KEY",
    }], ensure_ascii=False)

    def chain_responder(index, provider, images, prompt):
        if index == 0:
            raise module.AttemptError("http_500", "模拟服务端 500")
        return NATURAL_TEXT if index == 1 else STRUCTURED_TEXT

    calls, restore_call = patch_call_provider(module, chain_responder)
    restore_key = patch_load_key(module, {"DEEPSEEK_API_KEY", "OPENAI_API_KEY"})
    try:
        result = run_quiet(module, spec_for())
        error = None
    except Exception as exc:
        result, error = None, exc
    finally:
        restore_call()
        restore_key()
        os.environ.pop("VISION_FALLBACKS", None)
    statuses = [item.get("status") for item in (result.get("attempts") or [])] if result else []
    record("链路：第一家 500 → 第二家自然语言 → 整理重试成功；attempts 如实记录全过程",
           error is None and result and result.get("parsed") is True
           and result.get("provider") == "selftest-fallback"
           and statuses == ["http_500", "ok_after_repair"]
           and result.get("raw") == NATURAL_TEXT,
           "provider=%s attempts=%s" % (result.get("provider"), statuses)
           if result else str(error))

    # 10) 所有引擎都失败 → 如实抛可读错误，不许静默 ok=true
    def always_unauthorized(index, provider, images, prompt):
        raise module.AttemptError("http_401", "模拟鉴权失败")

    calls, restore_call = patch_call_provider(module, always_unauthorized)
    restore_key = patch_load_key(module, {"DEEPSEEK_API_KEY"})
    try:
        result = run_quiet(module, spec_for(provider="deepseek"))
        error = None
    except Exception as exc:
        result, error = None, exc
    finally:
        restore_call()
        restore_key()
    message = str(error) if error else ""
    record("所有视觉引擎都失败 → 抛出可读中文错误（点名 provider 与怎么自查），不静默成功",
           error is not None and result is None
           and "所有视觉引擎都调用失败" in message and "deepseek" in message,
           message[:120] if error else "竟然返回了 %s" % (result,))

    passed = sum(1 for item in RESULTS if item["ok"])
    total = len(RESULTS)
    print("\n" + "=" * 78)
    if passed != total:
        print("失败项：")
        for item in RESULTS:
            if not item["ok"]:
                print("  - %s：%s" % (item["name"], item["detail"][:200]))
    print("%d/%d passed" % (passed, total))
    print("=" * 78)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())