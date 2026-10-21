#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""求解内核离线自测（不联网、不需要图形环境）。

跑法（两个解释器都可以）：
    python test/symbolic_cases.py
    # 强制走 numpy 降级路径，验证"没有 sympy 时不许假装精确"
    $env:SYMBOLIC_NO_SYMPY=1; python test/symbolic_cases.py

判据分两类，这是刻意的：
* 有 sympy 时，每个用例都要求"算对 + 复核全过"；
* 降级（无 sympy）时，**只有**该环境真能做的 op 才要求算对；
  其余 op 只要求**诚实**——要么给出结果并声明"这是数值不是精确解"，要么明确进 unsupported，
  或者直接返回可读的中文错误。装出来的精确解才算失败。
"""

import json
import os
import subprocess
import sys

try:  # 控制台可能是 GBK：中文和上标符号都要能打出来
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "scripts", "symbolic.py")
NO_SYMPY = os.environ.get("SYMBOLIC_NO_SYMPY") == "1"


def run(spec):
    """调用内核，并把 stdout 解析成单个 JSON 对象。"""
    payload = json.dumps(spec, ensure_ascii=False)
    process = subprocess.run(
        [sys.executable, SCRIPT, "--spec-json", payload],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    lines = [line for line in (process.stdout or "").strip().splitlines() if line.strip()]
    if len(lines) != 1:
        return {"ok": False, "error": "stdout 不是单个 JSON（%d 行）：%r" % (len(lines), (process.stdout or "")[:400])}
    try:
        return json.loads(lines[0])
    except Exception as exc:
        return {"ok": False, "error": "stdout 不是合法 JSON（%s）：%r" % (exc, lines[0][:400])}


def checks_pass(value):
    failed = [item for item in value.get("checks", []) if not item.get("pass")]
    return (not failed), failed


RESULTS = []


def record(name, passed, detail=""):
    RESULTS.append((name, bool(passed), detail))
    print("%-4s %-56s %s" % ("PASS" if passed else "FAIL", name, detail))


def numeric_at(value, index=0):
    series = value.get("numeric") or [None] * (index + 1)
    return series[index]


def case(name, spec, verify=None, mode="both"):
    """通用用例。

    @param mode - both：两种环境都要求算对；
                  exact / unsupported：只有 sympy 路径要求算对。
                        降级路径只要求诚实（声明降级、或明确不可用、或给出可读错误）。
    """
    value = run(spec)
    if NO_SYMPY and mode in ("exact", "unsupported"):
        if not value.get("ok"):
            error = str(value.get("error"))
            good = any(ord(char) > 127 for char in error)   # 可读中文错误，而不是 traceback
            record(name + "［降级路径·诚实性］", good, "明确报错：%s" % error[:70])
            return value
        if mode == "unsupported":
            good = bool(value.get("unsupported"))
            detail = "如实声明不可用：%s" % (value["unsupported"][:1] or ["（空）"])
        else:
            good = bool(value.get("unsupported")) or bool(value.get("warnings")) or not value.get("exact")
            detail = "非精确但已声明：unsupported=%s" % (value.get("unsupported") or [])[:1]
        record(name + "［降级路径·诚实性］", good, detail)
        return value
    if not value.get("ok"):
        record(name, False, "内核报错：%s" % value.get("error"))
        return value
    ok, failed = checks_pass(value)
    if not ok:
        record(name, False, "复核未通过：%s" % json.dumps(failed, ensure_ascii=False)[:200])
        return value
    if verify is not None:
        try:
            good, note = verify(value)
        except Exception as exc:
            record(name, False, "校验抛异常：%s" % exc)
            return value
        record(name, good, note)
    else:
        record(name, True, "checks 全过（%d 项）" % len(value.get("checks", [])))
    return value


# ---------------------------------------------------------------- 代数与方程
case("一元二次（精确根 2 和 3）",
     {"op": "solve", "equations": ["x**2-5*x+6=0"], "unknowns": ["x"]},
     lambda v: (sorted(item["x"] for item in (v["numeric"] or [])) == [2.0, 3.0], str(v["exact"])))

case("一元二次（复根）",
     {"op": "solve", "equations": ["x**2+1=0"], "unknowns": ["x"]},
     lambda v: (all(isinstance(item["x"], dict) and abs(item["x"]["im"]) > 0.9 for item in (v["numeric"] or [])),
                str(v["exact"])), mode="exact")

case("三元线性方程组（相容）",
     {"op": "solve", "equations": ["x+y+z-6=0", "2*x-y+z-3=0", "x+2*y-z-2=0"],
      "unknowns": ["x", "y", "z"]},
     lambda v: (abs(numeric_at(v)["x"] - 1.0) < 1e-9 and abs(numeric_at(v)["y"] - 2.0) < 1e-9
                and abs(numeric_at(v)["z"] - 3.0) < 1e-9, str(numeric_at(v))))

case("非线性方程组（圆与直线）",
     {"op": "solve", "equations": ["x**2+y**2-5=0", "x-y-1=0"], "unknowns": ["x", "y"]},
     lambda v: (len(v["numeric"] or []) == 2, str(v["exact"])), mode="unsupported")

case("串联分压（物理·欧姆定律方程组，R1·I 是双线性）",
     {"op": "solve",
      "equations": ["U-12=0", "U-R1*I-R2*I=0", "R1-100=0", "R2-200=0"],
      "unknowns": ["U", "I", "R1", "R2"]},
     lambda v: (numeric_at(v).get("I") == 0.04 and numeric_at(v).get("U") == 12.0,
                "U=%s V, I=%s A" % (numeric_at(v).get("U"), numeric_at(v).get("I"))),
     mode="unsupported")

case("工科单字母当变量（I / E / N / Q，线性）",
     {"op": "solve", "equations": ["E-2*N-190=0", "N-5=0", "Q-4=0", "I-N-1=0"],
      "unknowns": ["I", "E", "N", "Q"]},
     lambda v: (abs(numeric_at(v)["I"] - 6.0) < 1e-9 and abs(numeric_at(v)["E"] - 200.0) < 1e-9,
                "I=%s E=%s（若被当成虚数单位/自然常数就会解错或解不出）"
                % (numeric_at(v)["I"], numeric_at(v)["E"])))

case("工科单字母参与乘积（I·E − N·Q）",
     {"op": "solve", "equations": ["I*E-N*Q=0", "E-200=0", "N-5=0", "Q-4=0"],
      "unknowns": ["I", "E", "N", "Q"]},
     lambda v: (abs(numeric_at(v)["I"] - 0.1) < 1e-12, "I = 20/200 = %s" % numeric_at(v)["I"]),
     mode="unsupported")

case("多字母符号不当乘积（RA / RB / q0，线性）",
     {"op": "solve", "equations": ["RA+RB-30=0", "4*RB-60=0", "q0-2*RA+30=0"],
      "unknowns": ["RA", "RB", "q0"]},
     lambda v: (abs(numeric_at(v)["RB"] - 15.0) < 1e-9 and abs(numeric_at(v)["q0"]) < 1e-9,
                "RA=%s RB=%s q0=%s（RA 与 RB 必须是两个独立符号）"
                % (numeric_at(v)["RA"], numeric_at(v)["RB"], numeric_at(v)["q0"])))

case("含符号参数的乘积式（RB·EI − 40·EI，带 positive 假设）",
     {"op": "solve", "equations": ["RA+RB-30=0", "RB*EI-40*EI=0"],
      "unknowns": ["RA", "RB"], "assumptions": {"EI": "positive"}},
     lambda v: (abs(numeric_at(v)["RB"] - 40.0) < 1e-9 and abs(numeric_at(v)["RA"] + 10.0) < 1e-9,
                "RA=%s RB=%s（EI 按参数处理并被约掉）" % (numeric_at(v)["RA"], numeric_at(v)["RB"])),
     mode="unsupported")

# ---------------------------------------------------------------- 微积分
case("参数化求解（含符号系数）",
     {"op": "solve", "equations": ["a*x+b=0"], "unknowns": ["x"]},
     lambda v: (v["exact"] and v["exact"][0]["x"] == "-b/a", str(v["exact"])), mode="unsupported")

case("三角方程 sin(x)=1/2",
     {"op": "solve", "equations": ["sin(x)-1/2=0"], "unknowns": ["x"]},
     lambda v: (len(v["numeric"] or []) >= 2, "%d 组解" % len(v["numeric"] or [])))

case("定积分 ∫0^1 x² dx = 1/3",
     {"op": "integrate", "expr": "x**2", "var": "x", "limits": [0, 1]},
     lambda v: (abs(numeric_at(v) - 1 / 3.0) < 1e-5,
                "数值 %s（sympy 路径的精确形式：%s）" % (numeric_at(v), (v["exact"] or ["—"])[0])))

case("定积分（超越函数，与 Simpson 对照）",
     {"op": "integrate", "expr": "exp(-x**2)", "var": "x", "limits": [0, 2]})

case("不定积分：求导应回到被积函数",
     {"op": "integrate", "expr": "x*sin(x)", "var": "x"}, mode="unsupported")

case("微分", {"op": "diff", "expr": "x**3*sin(x)", "var": "x"}, mode="unsupported")

case("高阶微分",
     {"op": "diff", "expr": "x**4", "var": "x", "order": 2},
     lambda v: (v["exact"][0] == "12*x**2", str(v["exact"])), mode="unsupported")

case("极限 sin(x)/x → 1",
     {"op": "limit", "expr": "sin(x)/x", "var": "x", "point": 0},
     lambda v: (abs(numeric_at(v) - 1.0) < 1e-12, str(numeric_at(v))), mode="unsupported")

case("极限 (1+1/x)^x → e",
     {"op": "limit", "expr": "(1+1/x)**x", "var": "x", "point": "oo"}, mode="unsupported")

case("幂级数展开 e^x",
     {"op": "series", "expr": "exp(x)", "var": "x", "point": 0, "order": 5}, mode="unsupported")

case("二阶常系数 ODE（带初值 y(0)=1, y'(0)=0）",
     {"op": "dsolve", "ode": "y'' + 4*y = 0", "func": "y", "var": "x",
      "ics": {"y(0)": 1, "y'(0)": 0}},
     lambda v: ("cos(2*x)" in v["exact"][0], str(v["exact"])), mode="unsupported")

case("一阶 ODE（无初值，应提示积分常数）",
     {"op": "dsolve", "ode": "y' - 2*y = 0", "func": "y", "var": "x"},
     lambda v: (any("积分常数" in json.dumps(item, ensure_ascii=False) for item in v["steps"]),
                str(v["exact"])), mode="unsupported")

case("梁挠曲线 ODE（机械·EI·v'' = -M）",
     {"op": "dsolve", "ode": "EI*v'' = -M", "func": "v", "var": "x",
      "ics": {"v(0)": 0, "v(1)": 0}},
     lambda v: ("M*x/(2*EI)" in v["exact"][0].replace(" ", ""), str(v["exact"])), mode="unsupported")

# ---------------------------------------------------------------- 线性代数
case("2×2 矩阵求逆",
     {"op": "matrix", "a": [[2, 1], [1, 3]], "action": "inverse"})

case("3×3 行列式",
     {"op": "matrix", "a": [[1, 2, 3], [4, 5, 6], [7, 8, 10]], "action": "det"},
     lambda v: (abs(numeric_at(v) + 3.0) < 1e-12, str(v["exact"][0])))

case("矩阵特征值",
     {"op": "matrix", "a": [[2, 0], [0, 3]], "action": "eigen"},
     lambda v: (sorted(numeric_at(v)) == [2.0, 3.0], str(numeric_at(v))))

case("矩阵解方程组 A·x = b",
     {"op": "matrix", "a": [[3, 1], [1, 2]], "b": [9, 8], "action": "solve"},
     lambda v: (abs(numeric_at(v)[0] - 2.0) < 1e-9 and abs(numeric_at(v)[1] - 3.0) < 1e-9,
                "x = %s（解向量按扁平列表回传）" % numeric_at(v)))

case("矩阵秩与行阶梯",
     {"op": "matrix", "a": [[1, 2], [2, 4]], "action": "rank"},
     lambda v: (numeric_at(v) == 1, "rank = %s" % numeric_at(v)))

# ---------------------------------------------------------------- 数值与数据
case("多项式求根（系数法）",
     {"op": "roots", "coefficients": [1, -6, 11, -6]},
     lambda v: (sorted(round(item, 6) for item in numeric_at(v)) == [1.0, 2.0, 3.0], str(numeric_at(v))))

case("表达式求值（保留精确分数）",
     {"op": "evaluate", "expr": "x**3 + y", "subs": {"x": "2/3", "y": 1}},
     lambda v: (v["exact"][0] == "35/27", "精确 %s / 数值 %s" % (v["exact"][0], numeric_at(v))),
     mode="exact")

case("最小二乘拟合（线性 + R²）",
     {"op": "fit", "x": [0, 1, 2, 3, 4], "y": [0.1, 1.9, 4.2, 5.9, 8.1], "degree": 1},
     lambda v: (v["steps"][0]["R²"] > 0.99, "R² = %.6f" % v["steps"][0]["R²"]))

case("二次拟合（抛物线与数值表）",
     {"op": "fit", "x": [0, 1, 2, 3, 4], "y": [1.0, 2.1, 5.2, 10.1, 17.2], "degree": 2},
     lambda v: (v["steps"][0]["R²"] > 0.999, "R² = %.6f" % v["steps"][0]["R²"]))

case("数值表（给可视化用）",
     {"op": "table", "expr": "x**2-1", "var": "x", "from": 0, "to": 2, "n": 5},
     lambda v: (len(numeric_at(v)) == 5 and numeric_at(v)[4]["value"] == 3.0, str(numeric_at(v)[4])))

case("一元极值（f''>0 判定极小）",
     {"op": "optimize", "expr": "x**2-4*x+7", "var": "x", "mode": "min"},
     lambda v: (abs(numeric_at(v)["x"] - 2.0) < 1e-9, str(numeric_at(v))))

case("区间极值（端点也是候选）",
     {"op": "optimize", "expr": "x**3-3*x", "var": "x", "range": [-3, 3], "mode": "max"},
     lambda v: (abs(numeric_at(v)["value"] - 18.0) < 1e-6, str(numeric_at(v))))

case("变形：因式分解",
     {"op": "factor", "expr": "x**2-1"},
     lambda v: (v["exact"][0] == "(x - 1)*(x + 1)", str(v["exact"])), mode="unsupported")

# ---------------------------------------------------------------- 安全与诚实
value = run({"op": "evaluate", "expr": "__import__('os').system('echo hi')", "subs": {"x": 1}})
record("拒绝注入式输入（可读中文原因）",
       value.get("ok") is False and "不允许" in str(value.get("error")), str(value.get("error"))[:70])

value = run({"op": "evaluate", "expr": "getattr(x, 'foo')", "subs": {"x": 1}})
record("拒绝属性访问", value.get("ok") is False, str(value.get("error"))[:70])

value = run({"op": "no_such_op"})
record("未知 op 给出可读错误",
       value.get("ok") is False and "未知 op" in str(value.get("error")), str(value.get("error"))[:60])

value = run({"op": "solve"})
record("缺 equations 不吐 traceback（只给可读原因）",
       value.get("ok") is False and "equations" in str(value.get("error")), str(value.get("error"))[:70])

value = run({"op": "integrate", "expr": "x**", "var": "x", "limits": [0, 1]})
record("语法坏掉的表达式给出可读错误",
       value.get("ok") is False and "\n" not in str(value.get("error")), str(value.get("error"))[:70])

value = run({"op": "solve", "equations": ["a*x+b=0"]})
if NO_SYMPY:
    # 降级路径下"自动识别出 3 个未知量、1 个方程"本就解不出，只要**说清楚**就算过关
    good = bool(value.get("exact")) or bool(value.get("unsupported")) or bool(value.get("warnings")) \
        or (value.get("ok") is False and isinstance(value.get("error"), str))
    detail = "降级路径：%s" % (str(value.get("unsupported") or value.get("error") or value.get("exact"))[:60])
else:
    good = value.get("ok") is True and bool(value.get("exact"))
    detail = str(value.get("exact"))[:60]
record("未指定未知量时自动识别", good, detail)

value = run({"op": "solve", "equations": ["x+y-1=0", "x+y-2=0"], "unknowns": ["x", "y"]})
# 无解/奇异方程组：要么说清"没有解"，要么给出可读错误 —— 就是不许假装成功给出一组数
empty_result = not value.get("exact") or all(not item for item in value.get("exact"))
record("无解方程组不许假装成功",
       (value.get("ok") is True and (empty_result or bool(value.get("warnings"))))
       or (value.get("ok") is False and isinstance(value.get("error"), str)),
       "ok=%s exact=%s warnings=%s" % (value.get("ok"), value.get("exact"), (value.get("warnings") or [])[:1]))

probe = run({"op": "probe"})
record("probe 如实报告 sympy 可用性",
       probe.get("ok") is True and bool(probe["steps"][0]["精确路径"]) != NO_SYMPY,
       "精确路径=%s sympy=%s" % (probe["steps"][0]["精确路径"], probe["steps"][0]["sympy"]))

# ---------------------------------------------------------------- 汇总
passed = sum(1 for _, ok, _ in RESULTS if ok)
total = len(RESULTS)
print("")
print("%d/%d passed  （%s）" % (passed, total, "numpy 降级路径" if NO_SYMPY else "sympy 精确路径"))
print(json.dumps(probe.get("steps", [{}])[0], ensure_ascii=False))
sys.exit(0 if passed == total else 1)