# 市场条目文案（LISTING）

本文件是插件在 DSH 社区索引/市场中的展示文案草稿，供发布者复制到索引条目、插件详情页或发布公告。**当前没有发布，也没有上架**；社区目录尚未上线（预计地址见 [README.md](README.md)，发布步骤见 [PUBLISH_PLAN.md](PUBLISH_PLAN.md)）。当前对应试用版本：`0.6.0-trial.6`。定位：**社区维护、Windows 试用版、非 DeepSeek 官方收录**。

## 标题

- 中文：**Moodle 学习助手**
- English: **Moodle Study Assistant**

> 与包内 `locale/zh.json`、`locale/en.json` 的 `meta.title` 保持一致：中文 `Moodle 学习助手`，英文 `Moodle Study Assistant`。
>
> 社区目录条目（`docs/market/index.json`）当前标题为中文 `Moodle 学习助手（Windows 试用版）`、英文 `Moodle Study Assistant (Windows trial)`，用于在目录中标注平台与试用定位；安装后的插件卡片标题仍取包内 `locale/*.json`。

## 一句话描述

- 中文：连接学校 Moodle 账号，在聊天里按章节学习保存在本机的课程资料。
- English: Connect your school Moodle account and study course chapters from materials saved on your own computer, inside the chat.

## 长描述（中文，条目详情页用）

Moodle 学习助手是一个 DeepSeek Harness 插件（社区维护的 Windows 试用版，非 DeepSeek 官方收录）：它直连你所在学校的 Moodle（只读），把课程资料保存在你自己选择的文件夹里，再在聊天中陪你按章节学习。

- **按章节学习**：指定章节时只准备和讲解该章——概念、符号、适用条件、例子和必要的原页核对，数学公式以可渲染的 LaTeX 呈现；不会把你的普通讲解自动变成备考，也不会一次丢出整章目录。
- **本机资料优先**：课件、笔记、错题和复习记录都在你的电脑上；插件安装目录不含个人数据，升级或卸载插件不会删除资料。
- **章节文件核对**：讲师课件更新后，助手会用 Harness 内的 Moodle 工具严格核对本章文件；同一聊天、同一章节且文件未变时不重复联网，只有你明确要求才重新核对。
- **学校信息按需更新**：只在当前请求确实需要作业要求、截止日期、公告或考试安排时，才读取学校最新信息并同步已授权资料。
- **设置与恢复能力**：设置进度保存在本机，任一步中断后重新打开聊天会从当前进度继续；学校暂时连不上时，本机资料读取、章节讲解和教学记录仍可用，联网内容会明确标注不是最新；同步中断后重跑是增量的，空目录或未完成的同步不会被显示为“设置完成”。
- **只读边界**：不提交作业、不代替你作答学校测验、不发帖、不修改学校完成状态、不发邮件；登录由你本人在学校网页完成，密码和验证码不进入聊天、日志或插件配置。

首次使用有引导：选择资料位置 → 安装运行依赖 → 本人登录学校 → 选择课程与资料范围（完整同步或只同步课程信息）→ 首次同步，之后即可开始学习。设置完成后也可以在聊天里换课程、换资料位置或修复运行依赖，旧资料目录默认保留。

## 长描述（English, for the entry detail page）

Moodle Study Assistant is a DeepSeek Harness plugin (a community-maintained Windows trial, not an official DeepSeek listing). It connects to your school's Moodle read-only, keeps course materials in a folder you choose, and studies chapter by chapter with you in the chat.

- **Chapter-scoped study**: prepare and explain only the chapter you name — concepts, symbols, conditions of use, examples and original-page checks when they matter. Maths is rendered in LaTeX. Ordinary explanations are not turned into exam revision, and a whole chapter is not dumped in one reply.
- **Local-first data**: course files, notes, mistakes and review records stay on your computer. The plugin directory holds no personal data, and upgrading or uninstalling the plugin does not delete your materials.
- **Chapter file verification**: when the lecturer's files change, the assistant verifies this chapter through the Moodle tool inside Harness. If the same chat and chapter are unchanged it reuses the previous strict receipt instead of reconnecting; a forced recheck only happens when you ask.
- **On-demand school information**: the assistant reads current assignments, deadlines, announcements or exam arrangements only when the request genuinely needs them, and syncs only materials you authorised.
- **Setup and recovery**: setup progress is kept locally, so an interrupted step resumes from where it stopped when you reopen the chat. During a temporary school outage, local material reading, chapter teaching and the teaching record keep working, and online information is clearly labelled as not the latest. Re-running an interrupted sync fills in what is missing; an empty folder or an unfinished sync is never shown as setup complete.
- **Read-only boundaries**: it never submits assignments, answers school quizzes for you, posts, changes completion status or sends email. Sign-in happens in your own browser on the school page; passwords and verification codes never enter the chat, logs or plugin configuration.

The first run is guided: choose a data location → install the runtime dependencies → sign in to your school yourself → choose courses and the material scope (full sync or information only) → first sync, then start studying. After setup you can also change courses or the data location, or repair the runtime, from the chat; the old data folder is kept by default.

## 关键词

`moodle` · `study` · `study assistant` · `course materials` · `tutoring` · `local-first` · `read-only` · `windows` · `community` · `deepseek harness` · `plugin`

中文可补：`学习助手` · `课程资料` · `章节讲解` · `本地资料` · `Moodle` · `社区插件`

## 分类与标签建议

| 字段 | 建议值 | 说明 |
| --- | --- | --- |
| `metadata.category` | `study` | 需在索引根 `metadata.categories` 声明；README 中已有示例。可按发布时市场实际分类调整 |
| `metadata.labels` | `windows`、`read-only`、`community` | 需在索引根 `metadata.labels` 声明；分别表示仅 Windows、学校接口只读、社区插件（非官方收录） |
| `metadata.keywords` | `moodle`、`study`、`course`、`tutoring`、`local materials`、`windows` | 自由文本数组，不要求声明 |
| `metadata.sortKeys` | `featured: 1`（示例） | 可选；排序键需在索引根 `metadata.sortKeys` 声明，并有 `order` |

当前 `docs/market/index.json` 已按上述字段写入：category `study`、labels `windows`/`read-only`/`community`、keywords `moodle`/`study`/`course`/`tutoring`/`local materials`/`windows`、`sortKeys.featured: 1`。

当前 DSH 的社区索引没有统一审核或官方分类体系，以上只是本模板的建议，发布时按实际索引约定调整。

## 图标说明

- 包内图标：`plugin/icon.svg`（882 字节 SVG，蓝色书页 + 对话气泡），用于已安装插件的卡片/详情/行，符合 DSH 显示元数据要求（SVG/PNG/JPEG/WebP，≤256 KiB，必须在包内，由 `package.json` 的 `exports` 提供 `./icon`）。
- 社区目录条目图标（已配置）：`docs/market/index.json` 的 `plugins[0].icon` 为相对路径 `icons/icon.svg`；`tools/release/build-market-site.mjs` 会把 `plugin/icon.svg` 复制为站点文件 `icons/icon.svg`。在预计 Pages 地址下解析为 `https://jiangzengyuang-jisoo.github.io/moodle-study-assistant/icons/icon.svg`（**目录尚未上线**）。
- 也可改用内嵌 `data:image/svg+xml;base64,…`（解码后 ≤32 KiB），但当前不采用；索引站已使用相对路径，图标与 `index.json` 同源。
- 当前**没有单独设计市场用 icon**；沿用包内 `icon.svg`（882 字节）即可，不建议另外制作低分辨率位图。

## 截图清单

**暂无截图。** 本次为试用版，桌面 UI 点击安装与首次设置的完整界面尚未截图（见发布说明「未验证」部分）。GitHub Pages 目录说明页目前也不引用任何外部图片资源。正式发布前建议补齐以下截图/录屏：

1. `Plugins → Add plugin` 安装对话框（含第三方信任提示）；
2. 安装完成后的插件卡片/开关（`Installed plugins`）；
3. 聊天内首次设置引导：选择资料位置、安装运行依赖、连接学校；
4. 浏览器中的学校登录页（只截登录入口，不含账号/密码/验证码）；
5. 选择课程与资料范围（完整同步/只同步课程信息）；
6. 一次章节讲解（含 LaTeX 公式）与章节核对结果提示；
7. 卸载确认与「个人资料保留」说明。

截图要求：不出现真实姓名、学号、课程私密内容、Token、密码或验证码；学校页面截图需确认学校允许公开。未实际验证的界面不要使用示意图冒充截图。

## 作者与主页

- `author`：当前为 `jiangzengyuang-jisoo`（`docs/market/index.json` 已填写，不再是占位）；发布前由维护者确认该名称可以公开。
- `homepage` / `repository`：`https://github.com/jiangzengyuang-jisoo/moodle-study-assistant`（准备阶段核实仓库为 public），`repository.directory` 为 `plugin`；发布前复查可公开访问。
- 版本与状态：社区目录条目版本 `0.6.0-trial.6`，npm 计划使用 `trial` tag；当前**未发布**，状态见 [README.md](README.md)。
- 许可证：MIT（见根目录 `LICENSE`）；第三方组件声明见 `THIRD_PARTY_NOTICES.md`。

## 相关文件

- [README.md](README.md)：目录/发布事实、确定方案与检查清单
- [PUBLISH_PLAN.md](PUBLISH_PLAN.md)：npm 发布与 GitHub Pages 部署执行清单
- [index.json](index.json)：社区目录源文件
- [pages/index.html](pages/index.html)：目录说明页源码
- [插件试用说明](../PLUGIN_TRIAL_GUIDE.md)：面向学生的安装与首次设置说明
