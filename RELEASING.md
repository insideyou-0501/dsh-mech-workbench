# 发版流程（RELEASING）

> 一句话：**改版本号 → 写 CHANGELOG → commit → 打 tag → push**。
> 照抄本文件的命令即可，不需要额外记忆。

---

## 一、发版前检查

```powershell
cd <本仓库路径>
git status --short          # 必须为空，没有未提交的改动
```

跑一遍自测（可选但推荐）：

```powershell
python test\run_all.py      # 或 node test\selftest.mjs
```

---

## 二、确定版本号

| 你改了什么 | 升哪一位 | 例子 |
|---|---|---|
| 修 bug、改文案、调整校验逻辑，**接口不变** | patch | 0.2.0 → 0.2.1 |
| **新增**工具 / 新增 `spec` 字段 / 新增 skill，旧用法仍有效 | minor | 0.2.1 → 0.3.0 |
| **破坏性变更**（见第六节），或达到稳定可用里程碑 | major | 0.3.0 → 1.0.0 |

规则：版本号只增不减；`package.json` 里写 `0.3.0`，tag 写 `v0.3.0`（**带 v，永远一致**）。

---

## 三、五步发版（以 0.3.0 为例）

### 第 1 步：改 `package.json` 的版本号

```powershell
(Get-Content package.json -Raw -Encoding UTF8) -replace '"version": "[^"]+"', '"version": "0.3.0"' | Set-Content package.json -Encoding UTF8 -NoNewline
```

也可以手动改，只要 `"version"` 变成 `"0.3.0"`。

### 第 2 步：改 `CHANGELOG.md`

在文件里**最上面那条版本记录之前**插入新一节（保持新版本在上）：

```markdown
## [0.3.0] - 2026-11-01

### Added
- 新增 XXX 工具 / XXX 能力

### Fixed
- 修复 XXX
```

### 第 3 步：扫一遍有没有漏改的旧版本号

```powershell
git grep -n "0.2.0"
```

输出里若出现 `package.json`、`CHANGELOG.md`、`README.md` 等处的旧版本号，逐个确认是否该改。
**不要**改 `.generations/` 或 profile 的 `package.json` —— 那是 `install_bundle` 的产物。

### 第 4 步：提交

```powershell
git add -A
git commit -m "release: 0.3.0"
```

### 第 5 步：打标签并推送

```powershell
git tag -a v0.3.0 -m "0.3.0"
git push --follow-tags
```

`--follow-tags` 会把提交和 `v0.3.0` 标签一起推上去，不用另跑 `git push --tags`。

> 成功标志：输出里出现 `main -> main` 和 `* [new tag] v0.3.0 -> v0.3.0`。

---

## 四、让本机用上新版本

发版只是"代码进了仓库"，**本机运行的仍是旧代次副本**。要生效需要重装一次：

1. 在 DSH 的**创造模式**会话里执行 `plugin_manager`，`action: install_bundle`，`target` 指向本仓库目录的绝对路径；
2. 重启 DSH（bundle 变更需重启）；
3. 确认版本：插件页显示的版本号应变新，或查
   `profiles/.generations/live/` 下新出现的 `dsh-mech-workbench+0.3.0+<hash>` 目录。

---

## 五、使用者如何拿到新版本

使用者自己 clone 的目录里执行：

```powershell
git pull
```

然后按第四节重新 `install_bundle` + 重启 DSH。

**查证"我到底用的是哪个版本"**：

```powershell
git describe --tags
```

- 输出 `v0.3.0` → 干净，就是 0.3.0 发布版；
- 输出 `v0.3.0-2-gabc1234` → 领先 tag 2 个未发版提交，说明代码不等于发布版。

---

## 六、哪些改动算破坏性变更（必须升 major）

这个插件的工具名与 `spec` 结构会被模型和用户直接引用，改动代价比普通库更高：

1. **重命名 / 删除工具**（如 `mech_diagram` → `mech_plot`）；
2. **改 `spec` 字段名或必填性**（如 `kind` 改成 `preset`）；
3. **改返回结构**（如 `pass` 字段改名、`answers` 变成对象）；
4. **改 `cordis.patch.yml` 里的 `id`** —— 宿主会当成新插件，导致重复挂载或卸载残留；
5. **改包名** `dsh-mech-workbench` —— profile 的 `dependencies` / `bundles` 全部指向旧名；
6. **删除或改名 `skills/` 下的目录** —— 技能按目录名注册；
7. **升级 `.pylibs` 里的依赖大版本**（如 sympy 2.x），可能改变符号解行为。

只新增、不修改既有字段的改动（新工具、新可选参数、新 skill）属于 minor，不在此列。

---

## 七、发版后可选操作

- **建 GitHub Release**：仓库页面 → Releases → Draft a new release → 选 `v0.3.0` → 把 CHANGELOG 那一节粘贴进去。作用：使用者能一眼看到最新版并下载 zip。
- **检查仓库体积**：`.pylibs` 约 27MB（已排除 `__pycache__`），属于正常范围；若某次提交异常变大，先查是否误提交了 `test/out/` 或 `__pycache__`。

---

## 八、出错回滚

| 情况 | 处理 |
|---|---|
| 提交错了，还没 push | `git reset --soft HEAD~1` 后重新提交 |
| 已 push 但内容有误 | `git revert <commit>` 生成一个反向提交（不要 force push 已发布的历史） |
| tag 打错了（还没 push） | `git tag -d v0.3.0` 后重新打 |
| tag 已 push 且需要撤 | 本地 `git tag -d v0.3.0`，`git push origin :refs/tags/v0.3.0`，再重新打并推送 |
| 新版本插件在本机出问题 | 在 `profiles/.generations/live/` 找到上一代次目录，用旧路径重新 `install_bundle` |