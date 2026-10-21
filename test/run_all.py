"""离线全量自测：一条命令跑完所有测试并汇总。

用法：
    python test/run_all.py

解释器约定：
  * 本脚本用**它自己所在的那个解释器**跑 Python 测试（`sys.executable`）。
  * 需要一个"没有 matplotlib"的解释器来验证 Pillow 降级路径，默认找 DSH 自带的
    Office 运行时；找不到就**如实跳过**（不算通过），可用环境变量指定：
        MECH_NOMPL_PYTHON=D:\\path\\to\\python.exe
  * Node 测试需要 `node` 在 PATH 上（DSH Desktop 自带的 Electron 也可以）。

不追求"全绿"的假象：跳过项单独列出，失败项给出标题，退出码非零。
"""
import os
import shutil
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
PY = sys.executable
NODE = shutil.which("node") or shutil.which("node.exe")

BUNDLED_NOMPL = os.path.join(
    r"D:\deepseek\DSH Desktop\resources\office-runtime\primary-runtime",
    "dependencies", "python", "python.exe",
)
PY_NOMPL = os.environ.get("MECH_NOMPL_PYTHON") or (
    BUNDLED_NOMPL if os.path.exists(BUNDLED_NOMPL) else None
)


def node_script(name):
    return [NODE, os.path.join(HERE, name)]


RUNS = []
if NODE:
    RUNS.append(("selftest.mjs（插件契约 + 无损 JSON 校验）", node_script("selftest.mjs"), {}))
    RUNS.append(("verify_installed.mjs（校验已安装产物）", node_script("verify_installed.mjs"), {}))
RUNS += [
    ("symbolic_cases.py（精确路径）", [PY, os.path.join(HERE, "symbolic_cases.py")], {}),
    ("symbolic_cases.py（强制降级路径）", [PY, os.path.join(HERE, "symbolic_cases.py")],
     {"SYMBOLIC_NO_SYMPY": "1"}),
    ("figure_cases.py（当前解释器的默认后端）", [PY, os.path.join(HERE, "figure_cases.py")], {}),
    ("figure_cases.py（强制 Pillow 后端）",
     [PY, os.path.join(HERE, "figure_cases.py"), "--force-backend", "pillow",
      "--out", os.path.join(HERE, "out", "presets_pillow")], {}),
    ("viz_cases.py（当前解释器）", [PY, os.path.join(HERE, "viz_cases.py")], {}),
    ("vision_cases.py（第二读者链路：json_object / 整理重试 / 绝不留丢转写）",
     [PY, os.path.join(HERE, "vision_cases.py")], {}),
]
if PY_NOMPL:
    RUNS.append(("figure_cases.py（无 matplotlib 的解释器 → Pillow 降级）",
                 [PY_NOMPL, os.path.join(HERE, "figure_cases.py"), "--expect-backend", "pillow",
                  "--out", os.path.join(HERE, "out", "presets_nompl")], {}))
    RUNS.append(("viz_cases.py（无 matplotlib 的解释器）", [PY_NOMPL, os.path.join(HERE, "viz_cases.py")], {}))
    RUNS.append(("reading_cases.py（读图预处理全算子）",
                 [PY, os.path.join(HERE, "reading_cases.py"), "--pillow-python", PY_NOMPL], {}))

# 程序化出图自检（缺字形 tofu / 版式裁切 / 亮度对比度），与 viz_check 的视觉评审互补：
# 它不依赖模型、结论确定，能查出"渲染成空心方框"这类视觉模型容易忽略的问题。
RUNS.append(("verify_cases.py（渲染产物程序化自检：缺字形/版式/亮度）",
             [PY, os.path.join(HERE, "cases", "verify_cases.py"),
              os.path.join(HERE, "out", "reading")], {}))

SKIPPED = []
if not NODE:
    SKIPPED.append("Node 测试（PATH 上没有 node）")
if not PY_NOMPL:
    SKIPPED.append("Pillow 降级路径（没找到无 matplotlib 的解释器，可用 MECH_NOMPL_PYTHON 指定）")

print("=" * 84)
print("工程题解工作台 · 离线全量自测")
print("Python：%s" % PY)
print("Node：  %s" % (NODE or "（没有）"))
print("=" * 84)

failures = []
for title, command, extra_env in RUNS:
    env = dict(os.environ)
    env.update(extra_env)
    try:
        procedure = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", env=env, cwd=PLUGIN, timeout=1800)
    except subprocess.TimeoutExpired:
        failures.append(title)
        print("-" * 84)
        print("[FAIL] %s   (超时)" % title)
        continue
    text = (procedure.stdout or "") + (procedure.stderr or "")
    tail = [line for line in text.strip().split("\n") if line.strip()]
    verdict = "PASS" if procedure.returncode == 0 else "FAIL"
    if procedure.returncode != 0:
        failures.append(title)
    print("-" * 84)
    print("[%s] %s   (exit=%d)" % (verdict, title, procedure.returncode))
    for line in tail[-3:]:
        print("    " + line[:150])

print("=" * 84)
for item in SKIPPED:
    print("跳过：%s" % item)
if failures:
    print("失败 %d 项：" % len(failures))
    for item in failures:
        print("  - " + item)
else:
    print("全部通过（%d 项%s）" % (len(RUNS), "，%d 项跳过" % len(SKIPPED) if SKIPPED else ""))
sys.exit(1 if failures else 0)