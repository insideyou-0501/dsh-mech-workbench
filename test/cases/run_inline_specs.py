#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_inline_specs.py —— 用 subprocess 精确传 argv，验证 `--spec-json '<inline>'` 契约。

（PowerShell 5.1 会把单引号参数里的双引号吃掉，命令行直接测不准，所以用 Python 传参。）

用法: <python> run_inline_specs.py <python_exe> <scripts_dir>
"""

import json
import os
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = HERE


def run(python_exe, script, spec_json):
    command = [python_exe, os.path.join(SCRIPTS, script), "--spec-json", spec_json]
    completed = subprocess.run(command, capture_output=True)
    stdout = completed.stdout.decode("utf-8", "replace").strip()
    stderr = completed.stderr.decode("utf-8", "replace").strip()
    parsed = None
    try:
        parsed = json.loads(stdout)
    except Exception:
        pass
    return {
        "script": script,
        "exit_code": completed.returncode,
        "stdout_is_json": parsed is not None,
        "ok": (parsed or {}).get("ok"),
        "stdout": parsed if parsed is not None else stdout[:400],
        "stderr_tail": stderr.splitlines()[-1] if stderr else "",
    }


def main(argv):
    global SCRIPTS
    python_exe = argv[0] if argv else sys.executable
    SCRIPTS = argv[1] if len(argv) > 1 else os.path.abspath(
        os.path.join(HERE, "..", "..", "scripts"))

    beam = os.path.join(CASES, "q1_beam.png")
    processed = os.path.join(CASES, "out", "p3_dark.png")
    out_dir = os.path.join(CASES, "out")

    checks = []
    checks.append(run(python_exe, "preprocess.py", json.dumps({
        "src": beam, "out": os.path.join(out_dir, "inline_test.png"),
        "ops": [{"op": "auto_orient"}, {"op": "grayscale"},
                {"op": "autocontrast", "cutoff": 1.0}],
        "max_side": 800,
    }, ensure_ascii=False)))
    checks.append(run(python_exe, "render_math.py", json.dumps({
        "original": processed,
        "transcription": "判断题：静定结构的内力与刚度 EI 无关。",
        "out": os.path.join(out_dir, "inline_render.png"),
        "width": 1200, "font_size": 26,
    }, ensure_ascii=False)))
    checks.append(run(python_exe, "render_math.py", json.dumps({
        "original": beam,
        "transcription": "手写体：重力 $W=mg$，其中 $m=5\\,\\mathrm{kg}$，$g=9.8\\,\\mathrm{m/s^2}$。",
        "out": os.path.join(out_dir, "inline_render2.png"),
        "width": 1200, "font_size": 24,
    }, ensure_ascii=False)))
    checks.append(run(python_exe, "vision.py", json.dumps({"doctor": True})))
    checks.append(run(python_exe, "vision.py", json.dumps({"doctor": "yes"})))

    for item in checks:
        if isinstance(item["stdout"], dict):
            item["stdout"] = {key: item["stdout"][key] for key in list(item["stdout"])[:8]}

    report = {"ok": all(item["exit_code"] == 0 and item["stdout_is_json"] for item in checks),
              "python": python_exe, "checks": checks}
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))