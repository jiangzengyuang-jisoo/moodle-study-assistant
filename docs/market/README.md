# 社区插件目录与发布材料（README）

本目录给维护者用，面向 DeepSeek Harness（DSH）插件生态。学生试用说明见 [插件试用说明](../PLUGIN_TRIAL_GUIDE.md)，插件发布说明见 [插件发布说明](../PLUGIN_RELEASE_NOTES.md)，第二阶段发布执行清单见 [PUBLISH_PLAN.md](PUBLISH_PLAN.md)，目录说明页源码见 [pages/index.html](pages/index.html)。

> **当前状态（截至 2026-11-12，当前准备版本 `0.6.0-trial.4`）：未发布。**
> 没有执行 `npm publish`，没有创建 `gh-pages` 分支，没有启用 GitHub Pages，也没有对外分享任何目录 URL。文中“预计 URL”目前**不可访问**；不得把本目录写成已上线、已上架或已被官方收录。

## 结论先行

1. **DSH 0.2.2-rc.1 没有官方插件市场索引。** 插件页的 `Discover` 标签页只在开启实验性插件索引功能（`Plugins → Experimental plugins → Custom plugin indexes`）后出现，并且只列出用户自己添加的索引源；随安装包编译的「官方 on-demand catalog」不是市场索引，profile 无法扩展它。
2. **「发布插件包」和「被索引收录」是两件事。**
   - **发布插件包**：把一个声明 `dsh.bundle` 的包发到 npm，或托管 Git 仓库／`.tgz` 文件。用户打开 `Plugins → Add plugin` 粘贴 `包名@版本`（或 Git 地址、`.tgz`、本地目录绝对路径）→ 阅读第三方信任提示 → `Install`，即可安装，**不需要任何市场或索引**。
   - **进入社区索引**：在「发布插件包」的基础上，另外生成并托管一个 format 1 的 `index.json`（本目录 [index.json](index.json) 为源文件）。用户开启实验性索引功能并添加该索引 URL 后，才能在 `Discover` 浏览和安装。**没有中心登记、官方审核或签名**。
3. **官方收录**（进入 DSH 安装包内置的官方可选/按需列表）只能随 DSH 官方仓库发版，第三方无法从 profile 侧扩展。本项目当前不走这条路线。
4. **本项目的确定方案（尚未执行）**：
   - **npm**：发布 `dsh-moodle-study@0.6.0-trial.4` 到 npm registry，**只打 `trial` tag**（不设 `latest`，不把试用版标成稳定版）；发布物由 `tools/release/pack-plugin.mjs` 打包并审计。
   - **GitHub Pages**：在仓库 `https://github.com/jiangzengyuang-jisoo/moodle-study-assistant`（public，默认分支 `codex`）新建**独立 orphan 分支 `gh-pages`**，站点根目录恰好 3 个文件：`index.json`、`index.html`、`icons/icon.svg`；不覆盖 `codex`/`deepseek`/`main`，不创建 GitHub Release。
   - **预计目录 URL**：`https://jiangzengyuang-jisoo.github.io/moodle-study-assistant/index.json`（**尚未上线**）
   - **预计说明页 URL**：`https://jiangzengyuang-jisoo.github.io/moodle-study-assistant/`（**尚未上线**）
   - 完整命令、验证、失败处理与回滚见 [PUBLISH_PLAN.md](PUBLISH_PLAN.md)；执行前需维护者本人明确授权。
5. **桌面版 profile 由 Electron 独占管理，不能用 CLI 装插件。** 发布者自己验收也要在 DSH 桌面版 `Plugins` 页操作，不要教学生使用命令行。

## 1. 已核实的 DSH 侧事实（0.2.2-rc.1）

| 事实 | 内容 | 依据 |
| --- | --- | --- |
| 安装入口 | 侧边栏 `Plugins` 页 → `Add plugin`；支持「包名[`@版本`]、Git 地址、tarball、绝对本地目录」四种 spec；安装前会显示第三方信任提示，需点 `Install` 确认 | `@deepseek-ai/dsh-client-ui-plugin-manager` 随包 README |
| 桌面 profile | 普通 CLI 管理 `desktop` profile 会被拒绝（`managed exclusively by the Electron application`）；桌面版由 Electron 自带 pnpm 处理安装 | DSH CLI 文档与实测 |
| 市场索引 | 无官方索引；`Discover` 只在启用实验性 bundle 后出现；索引 URL 必须是 `http(s)`、不含账号/密码/片段 | `dsh-experimental-plugin-market-bundle`、`dsh-plugin-market`、`dsh-client-ui-plugin-market` 随包 README |
| 官方收录 | 官方列表编译进 DSH 安装包，改列表需要新的 DSH 版本，profile 不能扩展 | `dsh-official-bundles` 随包 README |
| 索引格式 | format 1：根字段 `format`/`title`（LocalizedText，必须含 `en`）/`description`/`metadata`/`plugins`；每个版本必须有 `version`、`publishTimestamp`（Unix 秒整数）、`source`（`npm` 或 `git`，Git 需 40 位小写 commit）；`engines.dsh` 发布时务必声明 | `@deepseek-ai/dsh-plugin-market-index` 的 `parseMarketIndex` 校验规则 |
| 索引托管 | 静态托管即可：HTTP 2xx、无重定向、UTF-8、默认大小上限 128 MiB；索引不签名，`official` 之类的标记只是市场自称 | `dsh-plugin-market` 与用户指南 |
| 安装/卸载语义 | 安装后可默认自动启用，`application` 状态为 `applied` / `restart-required` / `overridden` / `failed` / `cancelled`（安装取消、profile 文件已还原）；卸载只还原 `package.json` 与 `pnpm-lock.yaml`，不删除插件写在 profile 之外的数据 | 插件管理文档 |
| 显示元数据 | `locale/<lang>.json` 的 `meta.title`/`meta.description`，可选 `meta.detail` 指向包内子目录 Markdown；图标为包内 SVG/PNG/JPEG/WebP，≤256 KiB；市场条目内嵌图标解码后 ≤32 KiB | `docs/cookbook/adding-a-package.md`、市场索引格式说明 |
| 版本兼容 | 包声明 `engines.dsh` 后只按该 range 检查，`peerDependencies` 被忽略；当前 DSH 版本可写 `^0.2.2-rc.1` 或 `>=0.2.2-rc.1 <0.3.0` | `dsh-app-boot` 随包 README |

> 本机调研档案（不随仓库发布、仅工作区留存）：`tmp/team/api-spec.md` §1（安装路径）、§6（市场事实）、§7（显示元数据）。上述事实以 DSH 随包文档与实测为准，DSH 升级后可能需要重新核对。

## 2. 发布产物与确定路线

### 2.1 构建与打包产物

- **插件包**：由 `tools/release/build-plugin.mjs` 构建到 `dist/moodle-study-assistant/`。构建脚本从 MCP 入口的 6 个直接运行时包出发，解析并内置**完整 Node 依赖闭包（当前 92 个包，无原生二进制）**，并扩写产物 `package.json` 的 `dependencies`/`bundleDependencies`，安装时不需要再下载 npm 依赖。`0.6.0-trial.4` 重新构建（2026-11-12）实测构建目录 **3559 个文件、15.1 MB**（含随包依赖闭包；`dist` 内的 tarball 不计入），个人数据、敏感路径与原生二进制审计通过；第一阶段 trial.3 构建为历史记录（3557 个文件、15.0 MB）。
- **npm tarball**：由 `tools/release/pack-plugin.mjs`（`npm run pack:plugin`）在构建后执行 `npm pack` 并审计打包后的字节，复制到 `release/npm/`。当前准备版本 `0.6.0-trial.4` 已生成 `release/npm/dsh-moodle-study-0.6.0-trial.4.tgz`：**3,190,465 字节（约 3.04 MB）、3558 个条目、92 个内置依赖包**，SHA-256 `cfd8fddd8ad2c48754dc135e6531d443c1c3bd08b024bea3d355545e502a9de7`；已在隔离目录解包并运行 `verify-plugin`（31/31 通过）。发布前复核哈希即可；若源码或打包内容再变化，必须重新运行 `npm run pack:plugin` 并重新复核，不能用 trial.3 的旧字节或哈希代替。第一阶段 trial.3 准备产物为历史记录：3,163,254 字节（约 3.02 MB）、3556 个条目、SHA-256 `6a1f6f57…`。审计会校验**完整依赖闭包**（不是硬编码 6 个包）：每个 vendored 包都必须在 `bundleDependencies` 中声明。
- **Pages 站点**：由 `tools/release/build-market-site.mjs`（`npm run build:market-site -- --published-at <Unix 秒>`）从 [index.json](index.json)、[pages/index.html](pages/index.html)、`plugin/icon.svg` 生成 `release/pages/`，恰好 3 个文件。`release/` 在 `.gitignore` 中，是本地产物，不提交到代码分支；部署走 `gh-pages`。

### 2.2 npm registry（确定路线）

| 项目 | 内容 |
| --- | --- |
| 包名 / 版本 | `dsh-moodle-study` / `0.6.0-trial.4` |
| npm tag | `trial`（`publishConfig.tag` 与发布命令都显式指定；**不加 `latest`**） |
| 发布物 | `release/npm/dsh-moodle-study-0.6.0-trial.4.tgz`（3,190,465 字节 / 3558 条目 / 92 个内置依赖包 / SHA-256 `cfd8fddd…`；已解包复核，`verify-plugin` 31/31 通过） |
| 发布命令 | `npm publish .\release\npm\dsh-moodle-study-0.6.0-trial.4.tgz --tag trial --access public`（见 [PUBLISH_PLAN.md](PUBLISH_PLAN.md) §2） |
| 学生安装 | 通过社区目录 `Discover` 安装（本方案首选）；或 `Add plugin` 填 `dsh-moodle-study@0.6.0-trial.4` |
| 升级方式 | 在 `Plugins` 页卸载旧版、安装新版；个人资料目录与运行支持目录默认保留。registry 来源若显示 `Versions`/`Update` 入口，以实际 UI 为准（本阶段未验证） |
| 失败处理 | 已发布版本不删除、不覆盖；内容错误用 `npm deprecate` 标注后发布下一个 trial 版本，详见执行清单 §2.4 |

其他分发方式（`Git` 仓库、`.tgz`/本地目录）仍可用于内部试用，但不属于本次公开发布方案；`.tgz`/本地目录为「链接/引用」或自包含副本，同包名直接重装会被 `already-installed` 拒绝。

### 2.3 GitHub Pages 社区目录（确定路线）

- **仓库与分支**：`https://github.com/jiangzengyuang-jisoo/moodle-study-assistant`。准备阶段核实：public，默认分支 `codex`，远端只有 `codex`/`deepseek` 两个分支、**没有 `gh-pages` 分支**；公开 API 查询 Pages 返回 404，与未启用 Pages 一致（发布前用 `gh api` 再复查）。部署时新建**独立 orphan 分支 `gh-pages`**，只推送 `index.json`、`index.html`、`icons/icon.svg` 三个文件；不动 `codex`/`deepseek`/`main`，不创建 GitHub Release。
- **目录条目**：`dsh-moodle-study@0.6.0-trial.4`，`source.type=npm`，`engines.dsh=^0.2.2-rc.1`，`icon: icons/icon.svg`（相对索引 URL，站点上解析为 `.../icons/icon.svg`）。条目文案见 [LISTING.md](LISTING.md)。
- **预计 URL（尚未上线）**：
  - 索引：`https://jiangzengyuang-jisoo.github.io/moodle-study-assistant/index.json`
  - 页面：`https://jiangzengyuang-jisoo.github.io/moodle-study-assistant/`
- **学生侧操作（写进用户文档，不替用户做）**：`Plugins → Experimental plugins` 打开 **Custom plugin indexes** → `Discover` 页 `Add index source` 填索引 URL → 浏览条目 → 在条目确认框确认图标/标题/版本/作者/来源/信任说明 → 安装。目录**不会自动出现**在所有人的 `Discover` 中，也没有官方审核或签名。
- 关闭实验性功能会隐藏 `Discover` 与 `plugin_market` 工具，但已保存的索引源会留在 profile。

### 2.4 发布前账号与授权要求

- **npm**：维护者本人 `npm login`（浏览器登录，2FA/OTP 本人输入）；账号需能发布 `dsh-moodle-study`。准备阶段 `npm view dsh-moodle-study` 返回 404（名称未被公开占用），发布前需复查。不记录、不导出 Token。
- **GitHub**：维护者本人 `gh auth login`（repo 权限）并 `gh auth setup-git`；`gh auth status` 正常；确认仓库 public、默认分支 `codex`、条目作者名 `jiangzengyuang-jisoo` 可公开。
- **发布范围**：npm 包与 Pages 站点均为公开内容；不创建 GitHub Release、不提交官方收录、不改版本号（版本变更需维护者决定）。
- **页面自包含与外部链接**：目录说明页的安装、升级、限制、隐私与故障恢复说明都写在页面正文中，不依赖尚未推送的代码分支；页面只链接仓库主页、同一仓库的问题页与 npm 包页（npm 包页发布完成后才可访问）。第二阶段只计划推送 `gh-pages`，**不推送** `codex`/`deepseek` 代码分支。

### 2.5 官方收录（本阶段不做）

官方收录 = 包成为 DSH 应用的 runtime dependency，声明 bundle patch、图标与 `locale` 元数据，被加入 DSH 官方仓库的 `OPTIONAL_BUNDLES`/`ON_DEMAND_BUNDLES` 列表，并随 DSH 发版。**这需要 DSH 官方仓库合并与发版，第三方无法从 profile 扩展**；本项目当前不提交官方收录。

## 3. index.json 字段说明与当前取值

`docs/market/index.json` 是社区目录的源文件（format 1），结构已按 DSH 校验器写全，并已更新为社区试用版定位：

- 根：`format: 1`、`title`/`description`（含 `en`、`zh`；标明社区维护、非官方索引）、`metadata.categories`/`labels`/`sortKeys`、`plugins`。
- 插件条目：`name`（`dsh-moodle-study`）、`title`、`description`、`icon`、`author`、`homepage`、`repository`（`url` + `directory: plugin`）、`metadata.category`、`metadata.labels`、`metadata.keywords`、`metadata.sortKeys`、`versions`。
- 版本条目：`version`、`publishTimestamp`（Unix 秒整数）、`source`（`{"type":"npm"}`）、`engines.dsh`。

当前关键取值：

| 字段 | 当前值 | 发布前要做的事 |
| --- | --- | --- |
| `plugins[0].author` | `jiangzengyuang-jisoo` | 维护者确认公开名后保留（已不是占位） |
| `plugins[0].homepage` | 项目仓库地址 | 确认可公开访问；与 `repository` 保持一致 |
| `plugins[0].repository` | `url` + `directory: plugin` | 与插件包 `package.json` 的 `repository` 语义一致 |
| `plugins[0].icon` | `icons/icon.svg` | 相对索引 URL；`build-market-site.mjs` 会把 `plugin/icon.svg` 复制到站点 `icons/icon.svg` |
| `versions[0].version` | `0.6.0-trial.4` | 与 npm 实际发布版本一致；发布后不可修改 |
| `versions[0].publishTimestamp` | `1794161475`（2026-11-09 02:11 HKT 的准备阶段值） | **发布时必须替换**：`build-market-site.mjs --published-at <真实时刻>` 会写入 `release/pages/index.json`；源文件保留准备值即可 |
| `versions[0].source` | `{"type":"npm"}` | 走 npm 时保持；若改 Git 需换成 40 位 commit 的规范 https 地址 |
| `versions[0].engines.dsh` | `^0.2.2-rc.1` | 与插件包 `package.json` 的 `engines.dsh` 保持一致 |
| 根与插件 `metadata` | 分类 `study`；标签 `windows`/`read-only`/`community`；排序键 `featured` | 引用未声明的分类/标签会校验失败；增删时两处同步 |

校验方式（DSH 自带校验器，准备阶段已实际运行通过）：

```powershell
$dshInstall = '<DeepSeek Harness 安装目录>'   # 本机实际安装目录
$env:DSH_ASAR_ROOT = "$dshInstall\resources\app.asar\dsh"
$env:ELECTRON_RUN_AS_NODE = '1'
& "$dshInstall\DeepSeek Harness.exe" tools/release/validate-market-index.mjs `
  --file docs/market/index.json `
  --url 'https://jiangzengyuang-jisoo.github.io/moodle-study-assistant/index.json'
```

准备阶段结果为 `"ok": true`；生成的 `release/pages/index.json` 也已用同一命令验证通过。发布时对**生成的站点文件**再验证一次，不能只验证源文件。

> 校验器只做结构校验，**不会**核对 npm 上是否真有对应版本或包内容是否可信；发布后仍要在开启 Custom plugin indexes 的 DSH 中实际添加索引验证。

## 4. 发布前检查清单

- [ ] 维护者已授权本次公开范围（npm 公开包 + gh-pages 公开站点 + 分享目录 URL）。
- [ ] `git status --short` 无意外改动；记录 `git rev-parse HEAD` 作为构建提交。
- [ ] `npm run pack:plugin` 通过（含构建、完整依赖闭包校验、个人数据审计、SHA-256）；`0.6.0-trial.4` 的 tarball 独立复核通过（第一阶段 `tmp/team/npm-pack-report.md` 为 trial.3 历史记录，本轮需重新复核）；记录 `version/entries/size/sha256`。
- [ ] `npm view dsh-moodle-study` 确认包名仍可用；`npm whoami` 为正确发布账号。
- [ ] `npm publish .\release\npm\dsh-moodle-study-0.6.0-trial.4.tgz --tag trial --access public --dry-run` 预览无异常。
- [ ] `npm run build-market-site -- --published-at <真实时刻>` 生成 `release/pages/`；核对恰好 3 个文件。
- [ ] 用 DSH 校验器验证 `release/pages/index.json`；核对 version、publishTimestamp、source、icon。
- [ ] 确认说明页自包含，外部链接仅为仓库主页/问题页与 npm 包页（npm 包页发布后可用，见 §2.4）。
- [ ] 在任何公开动作执行前，文档中不出现“已发布 / 已上架 / 已启用 Pages / 官方收录”的表述。
- [ ] 发布后按 [PUBLISH_PLAN.md](PUBLISH_PLAN.md) §2.3、§3.4、§4 验证，并填写其 §5 发布记录。

## 5. 仍需维护者本人授权后才能执行的步骤

以下步骤会对外产生影响，**本次交付没有执行，也不应自动执行**：

1. **`npm publish`**：把第三方插件代码公开发布到 npm registry（需要 registry 账号，可能涉及 OTP/双因素）。
2. **推送 `gh-pages` 并启用 GitHub Pages**：创建公开分支与公开站点 URL（本次方案见 [PUBLISH_PLAN.md](PUBLISH_PLAN.md)）。
3. **对外分发**：把包名、`.tgz`、Git 地址或目录 URL 发给其他学生或发布到社区（包括任何公开公告）；npm 包页在发布成功后才会存在。
4. **官方收录申请**（如未来决定走）：向 DSH 官方仓库提交包与列表变更，需随 DSH 发版。

本仓库只负责生成可验证的产物与文档；上述任一步骤执行前，维护者应先确认发布范围、版本号、许可证与第三方组件声明（`THIRD_PARTY_NOTICES.md`）没有变化。

## 6. 未验证与限制

- **准备阶段已实际运行（本地，无公开影响）**：临时运行 `build-market-site.mjs` 成功生成 3 个站点文件，并用 Harness 自带校验器对源索引与生成的站点索引验证通过（验证后已清理临时 `release/pages`，正式发布时重新生成）；`git ls-remote` 确认远端无 `gh-pages` 分支；`npm view dsh-moodle-study` 返回 404（名称未被公开占用）。
- **打包产物**：`release/npm/dsh-moodle-study-0.6.0-trial.4.tgz` 已生成（3,190,465 字节 / 3558 条目 / 92 个内置依赖包 / SHA-256 `cfd8fddd8ad2c48754dc135e6531d443c1c3bd08b024bea3d355545e502a9de7`），隔离解包后 `verify-plugin` 31/31 通过；第一阶段 trial.3 准备产物（3,163,254 字节 / 3556 条目 / SHA-256 `6a1f6f57…`）仅作历史记录。
- **未验证**：npm 发布本身与 registry 下载安装；推送 `gh-pages`、启用 Pages、Pages 构建/HTTPS 生效；从 `Discover` 添加索引到安装的完整链路；DeepSeek Harness 桌面 UI 鼠标点击安装的走查与截图；真实学校 SSO/MFA 登录与真实课程同步；其他学校、其他电脑、其他 Windows 账户。
- 预计目录/页面 URL **尚未上线**，任何文档都不得写成已可用；发布后需以线上实测结果更新本文档。
- DSH 为 0.2.2-rc.1，插件安装/市场机制可能随版本变化；发布前应以当前随包 README 与校验器重新核对。
- 社区索引没有官方审核与签名，DSH 不验证索引声明的包内容；安装第三方插件即信任其代码，责任在索引维护者与安装用户。

## 7. 相关文件

- [index.json](index.json)：社区目录源文件（format 1）
- [pages/index.html](pages/index.html)：目录说明页源码（内联样式、无外部资源）
- [PUBLISH_PLAN.md](PUBLISH_PLAN.md)：第二阶段精确执行清单（npm publish + gh-pages + Pages 启用 + 验证 + 回滚）
- [LISTING.md](LISTING.md)：条目标题、描述、关键词、图标与截图说明
- 构建脚本：`tools/release/build-plugin.mjs`、`tools/release/pack-plugin.mjs`、`tools/release/build-market-site.mjs`、`tools/release/validate-market-index.mjs`
- 学生与发布文档：[插件试用说明](../PLUGIN_TRIAL_GUIDE.md)、[插件发布说明](../PLUGIN_RELEASE_NOTES.md)
