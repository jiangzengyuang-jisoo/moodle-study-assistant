# 市场提交材料：发布事实与提交清单

本目录给维护者用，面向 DeepSeek Harness（DSH）插件生态。学生试用说明见 [插件试用说明](../PLUGIN_TRIAL_GUIDE.md)，本次发布说明见 [插件发布说明](../PLUGIN_RELEASE_NOTES.md)。

## 结论先行

1. **DSH 0.2.2-rc.1 没有官方插件市场索引。** 插件页的 `Discover` 标签页只在开启实验性插件索引功能（`Plugins → Experimental plugins → Custom plugin indexes`）后出现，并且只列出用户自己添加的索引源；随安装包编译的「官方 on-demand catalog」不是市场索引，profile 无法扩展它。
2. **「发布插件包」和「被索引收录」是两件事。**
   - **发布插件包**：把一个声明 `dsh.bundle` 的包发到 npm，或托管 Git 仓库／`.tgz` 文件。用户打开 `Plugins → Add plugin` 粘贴 `包名@版本`（或 Git 地址、`.tgz`、本地目录绝对路径）→ 阅读第三方信任提示 → `Install`，即可安装，**不需要任何市场或索引**。
   - **进入市场索引**：在「发布插件包」的基础上，另外生成并托管一个 format 1 的 `index.json`（本目录模板）。用户开启实验性索引功能并添加该索引 URL 后，才能在 `Discover` 浏览和安装。**没有中心登记、官方审核或签名**，索引可以被任何人复制或自行托管。
3. **官方收录**（进入 DSH 安装包内置的官方可选/按需列表）只能随 DSH 官方仓库发版，第三方无法从 profile 侧扩展。本项目当前不走这条路线。
4. **本目录不是已发布状态。** 模板中的占位字段必须在发布前替换（见 [§4](#4-indexjson-发布前必须替换的字段)）；`npm publish`、索引站点托管和对外公开都需要维护者本人另行授权后执行。
5. **桌面版 profile 由 Electron 独占管理，不能用 CLI 装插件。** 发布者自己验收也要在 DSH 桌面版的 `Plugins` 页操作，不要教学生使用命令行。

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

## 2. 两条发布路线

### 2.1 发布插件包（不需市场）

产物来自构建脚本 `tools/release/build-plugin.mjs`，输出 `dist/moodle-study-assistant/`（当前约 9.9 MB、2044 个文件，零 npm 安装依赖；随包内置 6 个 Node 运行时包）。

| 渠道 | 需要做的事 | 学生怎么装 | 升级方式 |
| --- | --- | --- | --- |
| npm registry | 去掉产物 `package.json` 的 `"private": true`，`npm pack` 检查文件清单后发布；`engines.dsh` 与插件显示元数据已声明 | `Add plugin` 填 `dsh-moodle-study@<版本>`（可展开选择 registry） | `Installed plugins` 出现 `Versions`/`Update`，或卸载重装 |
| Git 仓库 | 需要一个**可安装的包根**（内含 `package.json`、`cordis.patch.yml`、`locale/`、`icon`、`program/`）；仓库根必须指向该包（或索引里用 `path` 指向子目录），并记录 40 位 commit | `Add plugin` 填 Git 地址 | 卸载重装到新 commit |
| `.tgz` / 本地目录 | 把产物打成 `.tgz` 或直接分发解压后的目录 | `Add plugin` 填 `.tgz` 绝对路径或目录绝对路径 | 卸载后重装；同包名直接重装会被 `already-installed` 拒绝 |

注意：

- 本地目录安装是「链接/引用」形态，移动或删除源目录会让插件失效；`.tgz` 是自包含副本，适合分发给试用同学。
- 上述三种来源都不需要索引、不需要市场、不需要 DSH 方审批；但插件是第三方代码，用户安装时看到的信任提示是唯一确认点。
- 发布到 npm 或把包公开给其他人之前，先完成 [§4](#4-indexjson-发布前必须替换的字段) 的字段检查和 [§5](#5-发布前检查清单) 的清单，并确认没有把个人数据打进包（构建脚本已做个人数据与敏感路径审计）。

### 2.2 进入市场索引（自托管 format 1 索引）

1. 先完成 2.1 的「发布插件包」，拿到真实可安装的包名、版本和来源。
2. 复制本目录 [index.json](index.json) 模板，替换占位字段，并用 DSH 的校验器自检（见 §4）。
3. 把 `index.json`（以及可选的图标文件）托管到任意 HTTPS 静态站点；不需要中心登记，也没有官方审核。
4. 用户侧操作（写进用户文档，不要替用户做）：`Plugins → Experimental plugins` 打开 **Custom plugin indexes** → 在 `Discover` 页 `Add index source` 填索引 URL → 浏览条目 → 在条目确认框确认图标/标题/版本/作者/来源/信任说明 → 安装。
5. 关闭实验性功能会隐藏 `Discover` 与 `plugin_market` 工具，但已保存的索引源会留在 profile。

索引本身没有签名和背书；如果有人复制或改写这个索引，DSH 无法区分。市场条目的 `official` 类标记只是索引自称，不构成本项目或 DSH 官方的推荐。

### 2.3 官方收录（本阶段不做）

官方收录 = 包成为 DSH 应用的 runtime dependency，声明 bundle patch、图标与 `locale` 元数据，被加入 DSH 官方仓库的 `OPTIONAL_BUNDLES`/`ON_DEMAND_BUNDLES` 列表，并随 DSH 发版。**这需要 DSH 官方仓库合并与发版，第三方无法从 profile 扩展**；本项目当前不提交官方收录。

## 3. 索引模板字段说明

`docs/market/index.json` 是 format 1 模板，根字段与插件条目的结构已按 DSH 校验器写全：

- 根：`format: 1`、`title`（含 `en`，可加 `zh`）、`description`、`metadata.categories`/`labels`/`sortKeys` 声明、`plugins`。
- 插件条目：`name`（npm 合法包名，市场内唯一）、`title`/`description`（LocalizedText）、可选 `icon`、`author`、`homepage`、`repository`、`metadata.category`（必须引用根里声明的分类）、`metadata.labels`（必须引用根里声明的标签，不能重复）、`metadata.keywords`、`versions`。
- 版本条目：`version`（精确 SemVer）、`publishTimestamp`（Unix 秒整数）、`source`（`{"type":"npm"}` 或 `{"type":"git","url","commit","path"?}`）、`engines.dsh`（SemVer range）。
- 校验器会丢弃未定义字段、按版本降序排序、解析相对图标 URL；**不会**替你核对包内容。`engines.dsh` 缺失时校验器不报错（按格式说明视为不限，但市场格式说明要求发布时声明），非法的 range 会让该版本无法安装，因此发布前务必填写并自检。

## 4. index.json 发布前必须替换的字段

| 字段 | 模板当前值 | 发布前要做的事 |
| --- | --- | --- |
| `plugins[0].author` | `REPLACE_WITH_AUTHOR` | 换成真实维护者名称（必填占位） |
| `plugins[0].homepage` | 项目 README 中的仓库地址 | 确认它可公开访问；如不是公开主页则替换 |
| `versions[0].version` | `0.6.0-trial.3` | 与实际发布的包版本一致 |
| `versions[0].publishTimestamp` | `1794182400`（= 2026-11-09T00:00:00Z 的占位值） | 换成真实发布时间（Unix 秒整数） |
| `versions[0].source` | `{"type":"npm"}` 占位 | 走 npm 时保持（可加 `registry`）；走 Git 时替换为 `{"type":"git","url":"https://…","commit":"<40 位小写 SHA>","path":"<子目录，可选>"}`，Git URL 必须是无凭据、无查询/片段的规范 https 地址 |
| `versions[0].engines.dsh` | `^0.2.2-rc.1` | 与实际测试的 DSH 版本范围一致，并与包 `package.json` 的 `engines.dsh` 保持一致 |
| 根 `metadata` 与插件 `metadata` | 示例分类 `study`、标签 `windows`/`read-only`、排序键 `featured` | 按需增删；**引用未声明的分类/标签会校验失败**，删除引用后对应的声明也可删除 |
| 可选 `icon` | 模板未包含 | 需要条目图标时加：内嵌 `data:` SVG/PNG/JPEG/WebP（解码后 ≤32 KiB），或相对索引 URL／同源 https 的图片 URL |

> `0.6.0-trial.2` 对模板的改动是：版本号改为 `0.6.0-trial.2`、`publishTimestamp` 占位值改为 2026-11-09、插件描述补充“学校暂时连不上时仍可学习本地资料”；`0.6.0-trial.3` 只把版本号更新为 `0.6.0-trial.3`（补完整同步采集完整性修复），字段结构未变，发布前仍需按上表替换字段并重新校验。

本仓库已经用 DSH 自带的 `@deepseek-ai/dsh-plugin-market-index` 校验器对当前模板做过一次结构校验（使用一个 https 测试索引 URL），结果通过；替换字段后应重新校验：

- 方法：把 `index.json` 解析为 JSON，调用 `parseMarketIndex(value, indexUrl)`；抛 `MarketIndexError` 时按其 `path`/`reason` 修正。也可参考 DSH 仓库的 `apps/plugin-market-generator`（从包名/仓库列表生成 `index.json` 与图标目录）。
- 托管后建议在开启 Custom plugin indexes 的 DSH 里实际添加该 URL，确认 `Discover` 能读取条目、版本与 source facts，再交给学生。

## 5. 发布前检查清单

- [ ] 插件包：`package.json` 含 `name`、`version`、`type: "module"`、`exports`、`files`、`dsh.bundle.patch`、`engines.dsh`；npm 发布前删掉 `"private": true`。
- [ ] 插件包：`locale/en.json`（发现入口）与 `locale/zh.json` 的 `meta.title`/`meta.description` 正确，`meta.detail` 指向包内子目录 Markdown；图标为包内 SVG/PNG/JPEG/WebP 且 ≤256 KiB。
- [ ] 构建产物：跑一次 `tools/release/build-plugin.mjs`，确认个人数据审计通过（无 `Study/`、`.private/`、`config.local.json`、开发机绝对路径、姓名或 Token）。
- [ ] `npm pack --dry-run`（或实际 `npm pack`）确认文件列表包含 patch、`index.js`、`locale/*.json`、图标、`program/`、`docs/`，不包含开发用文件。
- [ ] `index.json` 占位字段已替换；用 `parseMarketIndex` 校验通过；索引站点为 HTTPS 且能直接返回 2xx。
- [ ] 在干净 profile 上实际走一遍：`Add plugin` 安装（本地/tarball 或 registry）→ 插件开关为 On → 首次设置能开始；若走索引，再验证 `Discover` 添加索引 → 条目 → 安装。
- [ ] 学生文档（[插件试用说明](../PLUGIN_TRIAL_GUIDE.md)）与实际 UI 文案一致；未验证项已标注。
- [ ] 对外发布相关动作已经维护者本人明确授权。

## 6. 仍需维护者本人授权后才能执行的步骤

以下步骤会对外产生影响，**本次交付没有执行，也不应自动执行**：

1. **`npm publish`**：把第三方插件代码公开发布到 npm registry（需要 registry 账号、可能涉及 OTP/双因素）。
2. **托管索引站点**：为 `index.json` 和图标准备 HTTPS 静态站（涉及域名/托管账号/成本），并把 URL 公开。
3. **对外分发**：把包名、`.tgz`、Git 地址或索引 URL 发给其他学生或发布到社区（包括 GitHub Release 等公开动作）。
4. **官方收录申请**（如未来决定走）：向 DSH 官方仓库提交包与列表变更，需随 DSH 发版。

本仓库只负责生成可验证的产物与文档；上述任一步骤执行前，维护者应先确认发布范围、版本号、许可证与第三方组件声明（`THIRD_PARTY_NOTICES.md`）没有变化。

## 7. 未验证与限制

- 本轮 `0.6.0-trial.2` 修复已在隔离 fixture 下完成独立模拟学校验收：8 项要求场景全部 PASS（101/101 项检查），附加回归 4/4 通过；覆盖设置中断续做、断网本地学习、同步完成判定与章节核对等。未模拟：真实学校 SSO/MFA 登录与浏览器登录组件、真实学校数据与权限受限内容、桌面 UI 点击安装、真实客户端自动重连、换 Windows 账户的凭据解密。
- **未验证**：market 索引从 `Add index source` 到 `Discover` 浏览、安装的完整链路（本机 desktop profile 未启用实验性索引功能，也没有添加索引源）；DSH 桌面 UI 点击安装的完整走查与截图；npm 发布；索引站托管；官方收录。
- **未验证**：真实学校账号登录后首次同步的完整体验（需学生本人完成 SSO/MFA），以及不同 Windows 电脑/账户、不同学校 Moodle 的安装表现。
- DSH 为 0.2.2-rc.1，插件安装/市场机制可能随版本变化；发布前应以当前随包 README 与校验器重新核对。
- 市场条目的安全责任在索引维护者与安装用户：DSH 不验证索引声明的包内容，安装即信任。
