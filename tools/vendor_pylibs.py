#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""纯 Python 依赖的「随包 vendoring」工具（不依赖 pip）。

为什么不用 pip：受限沙箱里 pip 的解包/改名步骤会被拒（`[Errno 13]`），
而「从 PyPI 下 wheel → 自己用 zipfile 解到 <plugin>/.pylibs」全程只是普通文件写入，
在任何沙箱下都能跑，而且产物随插件包一起走，装到别的机器上也不需要再联网。
（注意：这只适用于 `py3-none-any` 这种纯 Python wheel；带二进制扩展的包不要用本工具。）

用法::

    python tools/vendor_pylibs.py --list sympy mpmath
    python tools/vendor_pylibs.py --list sympy mpmath --dry-run
    python tools/vendor_pylibs.py --list sympy

默认目标目录是 <plugin>/.pylibs（即本脚本上一级的 .pylibs）。
`--prune` 会删掉目标目录里已经不在清单中的顶层包（谨慎使用）。
"""

import argparse
import io
import json
import os
import shutil
import sys
import urllib.request
import zipfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TARGET = os.path.join(PLUGIN_ROOT, ".pylibs")
PYPI_JSON = "https://pypi.org/pypi/{name}/json"
USER_AGENT = "dsh-mech-workbench-vendor/1.0"


def _get(url, timeout=120):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (固定 PyPI 域名)
        return response.read()


def _python_tag_rank(filename):
    """给 wheel 文件名打分：优先 py3-none-any（与解释器版本无关的纯 Python 包）。"""
    if filename.endswith("-py3-none-any.whl"):
        return 0
    if filename.endswith("-py2.py3-none-any.whl"):
        return 1
    if filename.endswith("-none-any.whl"):
        return 2
    return 99


def pick_wheel(meta):
    """从 PyPI 元数据里挑一个纯 Python wheel URL。"""
    urls = meta.get("urls") or []
    wheels = [item for item in urls if str(item.get("filename", "")).endswith(".whl")]
    if not wheels:
        raise RuntimeError("%s 没有 wheel 发行版（只有 sdist）" % meta["info"]["name"])
    wheels.sort(key=lambda item: _python_tag_rank(str(item["filename"])))
    best = wheels[0]
    if _python_tag_rank(str(best["filename"])) > 2:
        raise RuntimeError(
            "%s 只找到平台相关的 wheel（%s）；本工具只处理纯 Python wheel"
            % (meta["info"]["name"], best["filename"])
        )
    return best


def extract_wheel(data, target):
    """解 wheel 到目标目录；跳过 .dist-info/RECORD 之类的元数据以外的噪音。"""
    written = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            name = info.filename
            if name.endswith("/"):
                continue
            # 顶层目录形式（wheel 里通常没有），做一次防护
            parts = name.split("/")
            if any(part in ("..", "") for part in parts):
                continue
            if name.endswith(".dist-info/RECORD"):
                continue
            archive.extract(info, target)
            written += 1
    return written


def top_levels(names):
    """清单里每个发行版对应的顶层包/模块名（约定：包名去掉 - 与 . 后同名）。"""
    return sorted({name.replace("-", "_").replace(".", "_") for name in names})


def main(argv=None):
    parser = argparse.ArgumentParser(description="把纯 Python wheel 解到 <plugin>/.pylibs")
    parser.add_argument("--list", nargs="+", required=True, help="要 vendor 的发行版名，如 sympy mpmath")
    parser.add_argument("--target", default=DEFAULT_TARGET, help="目标目录（默认 <plugin>/.pylibs）")
    parser.add_argument("--dry-run", action="store_true", help="只报告将要做什么")
    parser.add_argument("--prune", action="store_true", help="删掉目标目录里不在清单中的顶层包")
    args = parser.parse_args(argv)

    names = list(dict.fromkeys(args.list))
    keep = set(top_levels(names))
    plan = []
    for name in names:
        meta = json.loads(_get(PYPI_JSON.format(name=name)).decode("utf-8"))
        wheel = pick_wheel(meta)
        plan.append(
            {
                "name": meta["info"]["name"],
                "version": meta["info"]["version"],
                "filename": wheel["filename"],
                "url": wheel["url"],
                "size": wheel.get("size"),
            }
        )

    for item in plan:
        sys.stdout.write(
            "计划 vendor：%s %s  <- %s（%.2f MB）\n"
            % (item["name"], item["version"], item["filename"], (item["size"] or 0) / 1048576.0)
        )
    if args.dry_run:
        sys.stdout.write("dry-run：未落盘。目标目录 %s\n" % args.target)
        return 0

    os.makedirs(args.target, exist_ok=True)
    os.makedirs(os.path.join(args.target, ".vendor-info"), exist_ok=True)

    report = []
    for item in plan:
        sys.stdout.write("下载 %s ...\n" % item["filename"])
        sys.stdout.flush()
        data = _get(item["url"])
        count = extract_wheel(data, args.target)
        report.append(
            {
                "name": item["name"],
                "version": item["version"],
                "filename": item["filename"],
                "files": count,
                "bytes": len(data),
            }
        )
        sys.stdout.write("  解出 %d 个文件\n" % count)

    if args.prune:
        for entry in sorted(os.listdir(args.target)):
            path = os.path.join(args.target, entry)
            if entry == ".vendor-info" or not os.path.isdir(path):
                continue
            if entry not in keep and not entry.endswith(".dist-info"):
                sys.stdout.write("prune：删除 %s\n" % path)
                shutil.rmtree(path, ignore_errors=True)

    info_path = os.path.join(args.target, ".vendor-info", "vendor.json")
    with open(info_path, "w", encoding="utf-8") as handle:
        json.dump({"target": args.target, "packages": report}, handle, ensure_ascii=False, indent=2)

    sys.stdout.write("完成：%s\n" % info_path)
    # 验证导入
    sys.path.insert(0, args.target)
    for name in names:
        module = name.replace("-", "_")
        try:
            mod = __import__(module)
            sys.stdout.write("  import %s -> %s\n" % (module, getattr(mod, "__version__", "?")))
        except Exception as exc:
            sys.stdout.write("  import %s 失败：%s\n" % (module, exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())