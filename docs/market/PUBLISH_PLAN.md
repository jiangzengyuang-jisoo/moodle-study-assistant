# 第二阶段发布执行清单：npm publish + GitHub Pages 社区目录

> **状态：第一阶段的准备交付，本清单中的任何公开动作都尚未执行。**
> 没有执行 `npm publish`，没有推送 `gh-pages`，没有启用 GitHub Pages，也没有对外分享目录 URL。
> 本文件只给维护者本人确认后使用；执行前必须逐项完成 §1 的门槛，并在 §5 记录实际结果。
> **本文件不包含、也不应记录任何 Token、OTP、密码或 npm/GitHub 凭据。** 需要交互登录时由维护者本人完成。

## 0. 目标状态

| 项目 | 目标值 |
| --- | --- |
| npm 包名 | `dsh-moodle-study` |
| 发布版本 | `0.6.0-trial.4`（已发布的版本号不可修改、不可覆盖） |
| npm tag | `trial`（**不使用 `latest`**，不把试用版标成稳定版） |
| 发布物 | `release/npm/dsh-moodle-study-0.6.0-trial.4.tgz`（2026-11-12 由 `pack:plugin` 生成并在隔离目录解包复核）：**3,190,497 字节（约 3.04 MB）、3558 个条目、92 个内置依赖包**，SHA-256 `50db3cb649bc7d56190411d91639a501d478f1d8e91bf33d1141c0a3ac775e87`。第一阶段 trial.3 准备产物为历史记录（3,163,254 字节 / 3556 条目 / SHA-256 `6a1f6f57…`），不能用于 trial.4。发布前只做哈希复核；若源码或打包内容再变化，必须重新运行 `pack:plugin` 并重新复核 |
| 源码仓库 | `https://github.com/jiangzengyuang-jisoo/moodle-study-assistant`（public，默认分支 `codex`） |
| Pages 分支 | 独立 orphan 分支 `gh-pages`（不覆盖 `codex`/`deepseek`/`main`） |
| Pages 站点文件 | 恰好 3 个：`index.json`、`index.html`、`icons/icon.svg` |
| 预计目录 URL | `https://jiangzengyuang-jisoo.github.io/moodle-study-assistant/index.json`（**尚未上线**） |
| 预计说明页 URL | `https://jiangzengyuang-jisoo.github.io/moodle-study-assistant/`（**尚未上线**） |
| 目录条目 | `dsh-moodle-study@0.6.0-trial.4`，`source.type=npm`，`engines.dsh=^0.2.2-rc.1`，`icon: icons/icon.svg` |
| 明确不做 | GitHub Release；DSH 官方收录；发布 `latest` 稳定 tag；在本清单中修改/推送 `codex`/`deepseek`/`main` 分支（第二阶段只推送 `gh-pages`，页面自包含、不依赖未推送分支，见 §1.4） |

公开影响：npm 包内容、作者名与仓库地址将对所有人可见；`gh-pages` 分支与 Pages 站点将对所有人可访问。二者是**公开发布动作**，不是普通仓库整理。

## 1. 执行前门槛（维护者本人确认）

### 1.1 授权范围

- [ ] 确认本次对外公开的范围：**npm 公开包 + GitHub Pages 公开站点 + 目录 URL 可分享**。
- [ ] 确认版本号 `0.6.0-trial.4`、`trial` tag 与“Windows 试用版”定位一致；本次**不改版本号**，不把试用版发布为稳定版。
- [ ] 确认不创建 GitHub Release、不提交 DSH 官方收录、不改现有分支页面。

### 1.2 账号（由维护者本人登录，不记录凭据）

```powershell
# npm：应为已登录的发布账号；未登录时执行 npm login，仅走浏览器交互
npm whoami
npm login          # 仅首次或会话失效时执行；2FA/OTP 由本人输入
npm view dsh-moodle-study version   # 准备阶段（2026-11-09）返回 404，说明名称未被公开占用；发布前必须复查
```

- [ ] npm 账号可发布 `dsh-moodle-study`（若 `npm whoami` 失败，先登录；若包已存在，停止并确认归属）。
- [ ] **不设置、不导出、不粘贴** `NPM_TOKEN`；不使用带 Token 的 registry URL。

```powershell
# GitHub：gh 未登录时执行 gh auth login（HTTPS + repo 权限），并配置 git 凭据助手
gh auth status
gh auth login      # 仅未登录时执行
gh auth setup-git  # 让 git push 使用 gh 的凭据，无需在 URL 中写 Token
gh repo view jiangzengyuang-jisoo/moodle-study-assistant --json visibility,defaultBranchRef
```

- [ ] GitHub 账号已登录且可推送该仓库；仓库仍为 public，默认分支仍为 `codex`。
- [ ] 确认条目作者名 `jiangzengyuang-jisoo` 可以公开使用。

### 1.3 产物与内容

- [ ] `git status --short` 没有意外改动；确认从哪个提交构建并记入 §5：`git rev-parse HEAD`。
- [ ] `release/npm` 中的 tarball 必须为本轮按 `0.6.0-trial.4` 生成并经独立复核的**冻结字节**；发布时直接使用该文件，不重新打包。本轮产物：`release/npm/dsh-moodle-study-0.6.0-trial.4.tgz`（3,190,497 字节 / 3558 条目 / 92 个内置依赖包 / SHA-256 `50db3cb649bc7d56190411d91639a501d478f1d8e91bf33d1141c0a3ac775e87`）；已在隔离目录解包，包内版本与修复文件核对通过，`verify-plugin` 对该解包产物 **31/31** 通过。第一阶段 trial.3 产物（SHA-256 `6a1f6f57…`）只是历史记录，不能用于 trial.4 发布。
- [ ] `docs/market/pages/index.html`、`docs/market/index.json` 与 `docs/market/PUBLISH_PLAN.md` 为本次准备版本；`index.json` 的 `author` 为 `jiangzengyuang-jisoo`，`versions[0]` 为 `0.6.0-trial.4`。
- [ ] 许可证（MIT）与 `THIRD_PARTY_NOTICES.md` 未变化；包内无个人数据（构建/打包脚本已审计）。

### 1.4 页面自包含与外部链接（重要）

目录说明页（`docs/market/pages/index.html`）的安装、升级、限制、隐私与故障恢复说明都写在**页面正文**中，不依赖任何未推送的代码分支或额外站点页面。

- 页面只保留可公开访问的外部链接：仓库主页 `https://github.com/jiangzengyuang-jisoo/moodle-study-assistant`（默认分支，正常可打开）、同一仓库的问题页 `/issues` 与 npm 包页 `https://www.npmjs.com/package/dsh-moodle-study`（**npm 发布成功后才可访问**）。
- **不要**把页面链接改回 `deepseek`/`codex` 分支的具体文件路径：第二阶段只计划推送 `gh-pages`，不推送代码分支，这些链接会 404。
- [ ] 发布前确认页面内没有指向未推送分支的链接，且正文已覆盖学生需要的安装/升级/限制/隐私/故障信息。
- **不要**为了本清单推送 `codex`/`deepseek`/`main`，除非维护者明确要求。
- 站点保持**恰好 3 个文件**：`index.json`、`index.html`、`icons/icon.svg`；不新增其他页面或资源文件。

### 1.5 时间戳

```powershell
$publishedAt = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$publishedAt
```

- [ ] 记下本次真实发布时刻的 Unix 秒，§3.1 生成站点时传给 `--published-at`。该值与 npm 发布时刻应尽量接近。

## 2. npm 发布（公开动作 #1）

### 2.1 核对冻结的 tarball（本地，无公开影响）

**`0.6.0-trial.4` 的 tarball 已于 2026-11-12 生成并在隔离目录解包复核**；发布前只做哈希/大小/条目数复核，不要重新打包（重新构建会刷新 `builtAt`，SHA-256 必然变化）。本轮记录：3,190,497 字节 / 3558 条目 / 92 个内置依赖包 / SHA-256 `50db3cb649bc7d56190411d91639a501d478f1d8e91bf33d1141c0a3ac775e87`；解包后 `verify-plugin` 31/31 通过。第一阶段 trial.3 准备产物为历史记录，不能用于 trial.4。

```powershell
$tgz = 'release\npm\dsh-moodle-study-0.6.0-trial.4.tgz'
Get-FileHash $tgz -Algorithm SHA256
# 期望：50db3cb649bc7d56190411d91639a501d478f1d8e91bf33d1141c0a3ac775e87
(Get-Item $tgz).Length            # 期望：3190203
(& tar -tzf $tgz | Measure-Object).Count   # 期望：3558
```

- [ ] SHA-256、大小、条目数与上述记录一致；不一致时**停止**，不发布（若确认源码/文档必须变化，则重新运行 `npm run pack:plugin` 并重新复核）。
- [ ] 包内 `package.json` 版本为 `0.6.0-trial.4`，`build-info.json` 的 `pluginVersion` 为 `0.6.0-trial.4`。

发布前预览（不产生公开影响）：

```powershell
npm publish .\release\npm\dsh-moodle-study-0.6.0-trial.4.tgz --tag trial --access public --dry-run
```

- [ ] 预览显示包名 `dsh-moodle-study`、版本 `0.6.0-trial.4`、tag `trial`、92 个 bundled deps、registry 为公开 npm。

### 2.2 发布到 npm（公开）

```powershell
npm publish .\release\npm\dsh-moodle-study-0.6.0-trial.4.tgz --tag trial --access public
```

注意：

- 必须显式写 `--tag trial`；**不要**加 `--tag latest`。包内 `publishConfig.tag` 也是 `trial`，但命令行仍显式写出。
- 只发布 §2.1 审计过的 tarball，不要在 `dist/` 内临时改动后发布。
- 2FA/OTP 按终端提示由本人输入；不把 OTP、密码、Token 写进命令历史、文件或聊天；如 shell 历史敏感，发布后自行清理。
- 该动作一旦成功，包内容与作者名对所有人可见，且同版本不可覆盖。

### 2.3 发布后验证（只读）

```powershell
npm view dsh-moodle-study@0.6.0-trial.4 version dist.tarball dist.integrity engines.dsh --json
npm view dsh-moodle-study dist-tags --json
```

预期：

- `version` 为 `0.6.0-trial.4`；`dist.tarball` 指向 npm registry；`dist.integrity` 存在；`engines.dsh` 为 `^0.2.2-rc.1`。
- `dist-tags` 中 `trial` 指向 `0.6.0-trial.4`；**不应**把 `latest` 指向本试用版。
- 首次发布若 npm 自动生成 `latest`：先停止后续步骤，由维护者决定是否执行 `npm dist-tag rm dsh-moodle-study latest`。该命令影响所有人可见的默认安装版本，需明确确认。
- registry 返回的 tarball 可能是 registry 侧重打包结果，**不要**用整包 SHA-256 与本地 tarball 比较；以 `dist.integrity`、文件清单和版本一致性为准。可在临时目录执行 `npm pack dsh-moodle-study@0.6.0-trial.4` 复核包内 `package.json` 版本与文件清单。

- [ ] 上述验证通过后，npm 状态记为“已发布（trial）”，再做 Pages。

### 2.4 npm 失败/回滚

- “返回非零但不确定是否成功”（超时、连接中断）：先 `npm view dsh-moodle-study@0.6.0-trial.4`；版本已存在说明发布成功，**不要重复发布**；版本不存在且确认失败才可重试同一命令。
- 版本冲突（`EPUBLISHCONFLICT`/`E409`）：说明该版本已存在，停止；不要改版本号强行重发，等待维护者决定是否发布下一个 trial 版本。
- 内容错误：npm 不支持覆盖同版本。使用 `npm deprecate dsh-moodle-study@0.6.0-trial.4 "<原因>"` 标注，再由维护者决定发布修复版本（如 `0.6.0-trial.5`）。
- tag 错误：用 `npm dist-tag add`/`npm dist-tag rm` 修正 tag 指向；改 tag 前确认影响。
- 包名被占用或权限不足：停止发布，确认账号与包名归属；**不要**改名或另起账号绕过。

## 3. GitHub Pages 部署（公开动作 #2）

前置：npm 已验证发布成功（目录条目的 `source` 为 npm；npm 未发布时不要上线索引）。

### 3.1 生成站点（本地，无公开影响）

```powershell
$publishedAt = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
npm run build:market-site -- --published-at $publishedAt
```

该脚本读取 `docs/market/index.json`、`docs/market/pages/index.html`、`plugin/icon.svg`，生成 `release/pages/`，并把 `publishTimestamp` 写成 `$publishedAt`、把相对图标保持为 `icons/icon.svg`。`release/` 已在 `.gitignore` 中，不会提交到代码分支。

预期输出：

```text
build-market-site: wrote release\pages
build-market-site: index.json version=0.6.0-trial.4 publishedAt=<时间戳>
build-market-site: icon=0.9 KiB, files=3
```

核对文件清单——**必须恰好 3 个文件**：

```powershell
Get-ChildItem release\pages -Recurse -File |
  ForEach-Object { $_.FullName.Substring((Get-Location).Path.Length + 1) }
# 预期：release\pages\index.json、release\pages\index.html、release\pages\icons\icon.svg
```

用 Harness 自带校验器验证站点索引（会按发布 URL 解析相对图标）：

```powershell
$dshInstall = '<DeepSeek Harness 安装目录>'   # 例如本机安装目录；路径含空格时保留引号
$env:DSH_ASAR_ROOT = "$dshInstall\resources\app.asar\dsh"
$env:ELECTRON_RUN_AS_NODE = '1'
& "$dshInstall\DeepSeek Harness.exe" tools/release/validate-market-index.mjs `
  --file release/pages/index.json `
  --url 'https://jiangzengyuang-jisoo.github.io/moodle-study-assistant/index.json'
```

预期输出 `"ok": true`，且：`version=0.6.0-trial.4`、`icon=https://jiangzengyuang-jisoo.github.io/moodle-study-assistant/icons/icon.svg`、`source.type=npm`、`engines.dsh=^0.2.2-rc.1`。

> 准备阶段已实际运行本脚本生成 3 个文件，并用上述校验器验证通过（对 `docs/market/index.json` 与生成的 `release/pages/index.json` 均 `ok: true`）。该临时产物已在验证后清理；正式发布时必须重新生成（写入真实 `publishTimestamp`）并再验证一次。

抽查 JSON 内容：

```powershell
node -e "const i=require('./release/pages/index.json');const v=i.plugins[0].versions[0];console.log(i.format,i.plugins[0].name,v.version,v.publishTimestamp,v.source.type,i.plugins[0].icon)"
```

- [ ] 版本、`publishTimestamp`（等于 §1.5 记下的时刻）、`source.type=npm`、`icon` 路径均正确。
- [ ] **上线前修改说明页状态行**：`docs/market/pages/index.html` 顶部当前写的是“发布状态：尚未上线（准备稿，截至 2026-11-12）”。在 npm 发布成功、准备启用 Pages 前，把它改为“发布状态：已发布（Windows 试用版，2026-11-12）”或同日等价表述，再执行 `build:market-site`；不要带着“尚未上线”部署。

### 3.2 推送到 `gh-pages` orphan 分支（公开）

原则：只创建/更新 `gh-pages`；不改 `codex`/`deepseek`/`main`；分支上只放 3 个站点文件；不创建 GitHub Release。

先检查当前状态并记录旧分支（用于回滚）：

```powershell
git fetch origin
git ls-remote --heads origin gh-pages   # 准备阶段已确认：远端只有 codex/deepseek，没有 gh-pages；若已有输出，先复制保存
```

用独立临时仓库推送（不切换、不修改当前工作区分支）：

```powershell
$repoUrl = 'https://github.com/jiangzengyuang-jisoo/moodle-study-assistant.git'
$stage = Join-Path $env:TEMP 'moodle-study-gh-pages'
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $stage | Out-Null
git -C $stage init
git -C $stage remote add origin $repoUrl

# 已有 gh-pages 时基于它更新；首次部署时创建 orphan 分支
git -C $stage fetch origin gh-pages 2>$null
if ($LASTEXITCODE -eq 0) {
  git -C $stage checkout -b gh-pages FETCH_HEAD
} else {
  git -C $stage checkout --orphan gh-pages
}

# 清空临时工作区，只放 3 个站点文件
Get-ChildItem $stage -Force | Where-Object { $_.Name -ne '.git' } | Remove-Item -Recurse -Force
Copy-Item release\pages\index.json $stage -Force
Copy-Item release\pages\index.html $stage -Force
Copy-Item release\pages\icons $stage\icons -Recurse -Force

git -C $stage add -A
git -C $stage commit -m 'deploy: community plugin index dsh-moodle-study@0.6.0-trial.4'
git -C $stage ls-tree -r --name-only HEAD   # 必须恰好是 index.json / index.html / icons/icon.svg
git -C $stage push origin gh-pages
```

- [ ] 推送前检查 `git ls-tree` 输出：**只有 3 个文件**。
- [ ] `git push` 成功；若失败，优先检查 `gh auth status` 与 `gh auth setup-git`，重试推送本分支；不要 force push 其他分支。
- [ ] 记录本次 `gh-pages` commit SHA：`git -C $stage rev-parse HEAD`（写入 §5）。

### 3.3 启用 GitHub Pages（公开）

```powershell
$pagesApi = 'repos/jiangzengyuang-jisoo/moodle-study-assistant/pages'

# 1) 检查是否已启用；未启用时返回 404
gh api $pagesApi 2>&1

# 2) 首次启用（分支来源、站点根目录 /）：
gh api --method POST $pagesApi -f 'source[branch]=gh-pages' -f 'source[path]=/'

# 3) 已启用但需要更新来源时：
gh api --method PUT $pagesApi -f 'source[branch]=gh-pages' -f 'source[path]=/'
```

等待构建完成（`status` 从 `building` 变为 `built`；通常 1–5 分钟）：

```powershell
for ($i = 0; $i -lt 20; $i++) {
  $status = gh api $pagesApi --jq '.status' 2>$null
  Write-Host "Pages status: $status"
  if ($status -eq 'built') { break }
  Start-Sleep -Seconds 15
}
gh api $pagesApi --jq '{html_url, status, source, https_enforced}'
```

- [ ] `html_url` 为 `https://jiangzengyuang-jisoo.github.io/moodle-study-assistant/`；`source.branch=gh-pages`、`source.path=/`。
- [ ] `status` 为 `built`；`https_enforced` 为 `true`（GitHub Pages 对 github.io 默认强制 HTTPS；证书签发可能还需几分钟）。

### 3.4 验证线上地址（只读）

```powershell
$base = 'https://jiangzengyuang-jisoo.github.io/moodle-study-assistant'
$tmp = Join-Path $env:TEMP 'moodle-pages-verify'
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
curl.exe -sS --max-redirs 0 -D "$tmp\index.h" -o "$tmp\index.json" "$base/index.json"
curl.exe -sS --max-redirs 0 -D "$tmp\page.h" -o "$tmp\page.html" "$base/"
curl.exe -sS --max-redirs 0 -D "$tmp\icon.h" -o "$tmp\icon.svg" "$base/icons/icon.svg"

Select-String -Path "$tmp\index.h","$tmp\page.h","$tmp\icon.h" -Pattern '^HTTP/','^content-type:','^location:'
Select-String -Path "$tmp\page.html" -Pattern '社区插件目录','非 DeepSeek 官方收录','Windows 试用版'

$json = Get-Content "$tmp\index.json" -Raw | ConvertFrom-Json
$json.plugins[0] | Select-Object name, author, icon
$json.plugins[0].versions[0] | Select-Object version, publishTimestamp, source, engines
```

- [ ] `index.json` 返回 **200**、`Content-Type` 合理（如 `application/json; charset=utf-8`）、无 3xx 重定向、JSON 可解析。
- [ ] 页面返回 200 且包含页面关键文案；`icons/icon.svg` 返回 200（`image/svg+xml`）。
- [ ] `version=0.6.0-trial.4`、`publishTimestamp` 为本轮值、`source.type=npm`。
- 若首次返回 404 或旧内容：先等几分钟（Pages 构建或 CDN 缓存），不要立即重复推送；确认真实失败后再排查。

### 3.5 Pages 失败/回滚

- Pages **构建失败**：`gh api $pagesApi --jq '.status'` 为 `errored` 时查看构建信息；常见原因是分支不存在或站点文件路径错误。修正后重新推送 `gh-pages` 即可触发重建。
- **内容错误回滚**（优先保留历史，不强制重写分支）：按发布前记录的旧 `gh-pages` SHA 恢复：
  ```powershell
  $oldSha = '<发布前记录的 gh-pages SHA>'
  git -C $stage fetch origin gh-pages
  git -C $stage checkout -B gh-pages FETCH_HEAD
  git -C $stage checkout $oldSha -- index.json index.html icons/icon.svg
  git -C $stage add -A
  git -C $stage commit -m 'revert: restore previous community plugin index'
  git -C $stage push origin gh-pages
  ```
  首次部署即出错、没有旧版本可回退时，由维护者决定推送占位页或**禁用 Pages**。
- **紧急下线**：`gh api --method DELETE repos/jiangzengyuang-jisoo/moodle-study-assistant/pages` 会停止 Pages 服务（公开影响，需维护者确认）；之后 `gh-pages` 分支可保留或另行删除，删除分支前确认没有其他用途。
- 回滚/下线后，URL 与 CDN 缓存可能短暂保留旧内容，等待并重新验证后再对外说明。

## 4. 发布后验证（公开后）

### 4.1 目录 → 安装的完整链路（人工 UI）

由维护者或一名试用同学在 DeepSeek Harness 桌面版完成：

1. `Plugins` → `Experimental plugins` → 打开 **Custom plugin indexes**；
2. `Discover` → **Add index source** → 粘贴 `https://jiangzengyuang-jisoo.github.io/moodle-study-assistant/index.json`；
3. 确认出现条目「Moodle 学习助手（Windows 试用版）」，版本 `0.6.0-trial.4`、作者 `jiangzengyuang-jisoo`、来源 npm；
4. 在条目页/确认框阅读第三方信任提示并安装；确认插件开关为 On；
5. 新开聊天说“帮我设置 Moodle 学习助手”，确认设置工具出现（至少到资料位置/运行依赖步骤，不需要真实学校登录即可验证工具注册）。

- [ ] 上述 5 步实际完成并记录日期、Harness 版本与截图（如方便；截图不含个人信息）。
- [ ] 若第 3 步条目缺失：检查索引 URL、`format`、`engines.dsh` 与 Harness 版本是否匹配，再检查页面与索引是否同源更新。

### 4.2 registry 路径的隔离验证（可选，不能操作 desktop profile）

沿用第一阶段隔离 profile 的流程（见 `tmp/team/npm-pack-report.md`，含 pnpm shim 与 profile 清理），把来源从本地 tarball 换成 registry 包名：

```powershell
$install = '<DeepSeek Harness 安装目录>'   # 本机实际安装目录
$env:ELECTRON_RUN_AS_NODE = '1'
& "$install\DeepSeek Harness.exe" "$install\resources\app.asar\dsh\node_modules\@deepseek-ai\dsh\lib\bin.js" `
  plugin --profile market-verify add 'dsh-moodle-study@0.6.0-trial.4'
```

- 该 CLI 会转发给 pnpm；若 PATH 中没有 pnpm，使用第一阶段准备的 `tmp/pnpm-shim`（不要把 pnpm 装进系统全局配置来绕过）。
- 验证完成后删除自己创建的隔离 profile；**不要**操作 desktop profile。
- 记录安装到的包版本与工具注册结果；真实 DeepSeek Harness 桌面版安装仍以 §4.1 为准。

## 5. 记录模板（发布完成后填写）

| 项目 | 记录 |
| --- | --- |
| 准备/构建提交 SHA | `89fdbcb8df7e626ae218d669839f3bbf5d25bad3`（tarball 的 `build-info.builtFromCommit`；文档记录提交另见 git log） |
| pack：version / entries / size / sha256 | `0.6.0-trial.4` / 3558 / 3,190,497 bytes / `50db3cb649bc7d56190411d91639a501d478f1d8e91bf33d1141c0a3ac775e87`（2026-11-12） |
| npm publish 时间（HKT / UTC） | |
| `npm view` 验证结果（version / integrity / dist-tags） | |
| `gh-pages` commit SHA | |
| Pages `html_url` / `status` / 来源 | |
| `index.json` URL：HTTP 状态 / Content-Type | |
| 页面 URL：HTTP 状态 / 摘要 | |
| 实际完成的验证范围（§4.1 / §4.2） | |
| 未完成、失败或未验证项 | |

## 6. 明确不做 / 禁止事项

- 不创建 GitHub Release，不发布 `latest` 稳定 tag，不提交 DSH 官方收录。
- 不修改 `codex`/`deepseek`/`main` 分支内容；第二阶段只推送 `gh-pages`（见 §1.4）。
- 不修改已发布版本号；不删除、不覆盖已发布的 npm 版本（内容错误用 `npm deprecate` + 新版本处理）。
- 不把 Token、OTP、密码写进命令、文件、聊天或 Git 提交；不使用带凭据的 URL。
- 不为了“重试”而重复执行已成功的 npm publish；每个公开动作成功后先验证，再进入下一步。
- 不把 `release/pages`（本地产物）提交到代码分支；部署只走 `gh-pages`。
- 不在完成 §2.3、§3.4、§4.1 验证之前对外声称“已发布/已上架/已启用 Pages/官方收录”。失败时如实保留已完成与未完成状态。

## 7. 准备阶段已验证 / 未验证

**准备阶段已实际运行（本地，无公开影响）**

- `node tools/release/build-market-site.mjs --published-at <时间>`：临时生成 `release/pages/` 的 3 个文件（index.json / index.html / icons/icon.svg）；验证后已清理该临时产物，正式发布时重新生成。
- `tools/release/validate-market-index.mjs` 使用 Harness 自带 `parseMarketIndex` 校验：`docs/market/index.json` 与生成的 `release/pages/index.json` 均返回 `"ok": true`，相对图标解析为预计 Pages URL。
- `docs/market/index.json` 的版本、作者、repository、icon 字段与 `0.6.0-trial.4` 一致；`npm view dsh-moodle-study` 在准备阶段返回 404（名称未被公开占用）。
- `pack-plugin.mjs`、`build-market-site.mjs`、`validate-market-index.mjs` 均存在并在仓库内；`release/npm/dsh-moodle-study-0.6.0-trial.4.tgz` 已生成（3,190,497 字节 / 3558 条目 / 92 个内置依赖包 / SHA-256 `50db3cb6…`），解包后 `verify-plugin` **31/31** 通过；第一阶段的 trial.3 产物（3,163,254 字节 / 3556 条目 / SHA-256 `6a1f6f57…`）仅作历史记录。`npm`/`git`/`gh`/`curl.exe` 在本机可用。

**未验证（必须完成后才能对外说明）**

- `npm publish` 本身、registry 下载安装、`npm view` 的最终结果。
- 推送 `gh-pages`、启用 Pages、Pages 构建与 HTTPS 生效。
- 从 `Discover` 添加真实索引并安装的完整链路、桌面 UI 点击安装的走查与截图。
- 真实学校账号登录与课程同步（需要学生本人完成）。
- 不同学校、不同电脑、不同 Windows 账户的安装表现。
