# Moodle 学习助手插件试用版发布说明

- 发布对象：DeepSeek Harness 插件包 **`dsh-moodle-study@0.6.0-trial.1`**
- 日期：2026-11-08（香港时间）
- 状态：**试用版**。尚未发布到 npm，也未进入任何市场索引；本说明描述的是仓库内的可安装产物与已验证范围，不表示已经公开发布或上架。
- 配套文档：[插件试用说明](PLUGIN_TRIAL_GUIDE.md) · [市场提交材料](market/README.md)

## 1. 本次发布做了什么

本次把原来需要项目文件夹和命令行的 Moodle 学习助手，改造成一个可从 DeepSeek Harness 插件页安装的第三方 bundle：

1. **可安装的插件包**：包名 `dsh-moodle-study`，插件源码在 `plugin/`，构建脚本 `tools/release/build-plugin.mjs` 输出自包含的 `dist/moodle-study-assistant/`（当前约 **9.8 MB、2042 个文件**）。插件包零 npm 安装依赖，随包内置 6 个 Node 运行时包；Python 运行依赖由插件首次使用时安装。
2. **聊天内首次设置**：新增设置与恢复工具 `study_setup_status`、`study_setup_location`、`study_setup_runtime`、`study_connect_school`、`study_setup_courses`、`study_setup_select_courses`、`study_setup_sync`、`study_setup_finish`、`study_job_status`，把「选资料位置 → 装运行依赖 → 本人登录学校 → 选课程与资料范围 → 首次同步 → 自动重连」串成有状态的引导流程，失败可重试、不覆盖已有资料。
3. **教学能力接入插件**：注册 `self-study-tutor` 教学技能、`moodle-study` 使用技能，以及常驻教学规则（一次讲透一个适量知识点、可渲染 LaTeX、指定章节不扩大范围、回复末尾不堆技术日志）。没有项目根 `AGENTS.md` 的新用户也会得到这套行为。
4. **章节核对走 MCP 工具**：`study_check_chapter` 保持严格内容哈希核对；同一聊天、同一章节且本地文件未变时复用回执、不联网，本地新增本章资料只核对新增范围；只有学生明确要求才强制重查。异常只在工具未加载或 MCP 启动/传输故障时走 CLI 后备，受限解密错误每操作最多申请一次单次授权。
5. **本地学习工具（无需 shell）**：`study_local_context`、`study_chapter_prep`、`study_material_read`、`study_material_render`、`study_teaching_event`、`study_local_status`、`study_refresh`、`study_job_status`；Host 侧另有 `moodle_study_chat`（取当前聊天标识）和 `moodle_study_status`（MCP 启动失败时仍可读取设置状态并给出恢复步骤）。
6. **程序与个人数据分离**：程序在插件包内；学习资料、配置和 Windows DPAPI 加密的学校凭据放在学生选择的资料目录（布局仍是 `<资料目录>/Study/`）；运行环境、日志和设置状态放在 `%LOCALAPPDATA%\MoodleStudyAssistant`。升级或卸载插件默认保留个人资料。
7. **引擎支持独立数据根**：新增 `STUDY_DATA_ROOT` 环境变量覆盖（未设置时行为与旧版一致），Node/Python 引擎按程序根定位自身文件、按数据根读写学生数据；仓库新增对应回归测试 `tests/data-root.test.mjs`、`tests/test_data_root.py`。

## 2. 用户可见变化

| 之前 | 现在 |
| --- | --- |
| 需要项目文件夹、安装步骤和多条命令，安装与启动门槛较高 | 在 DSH 插件页 `Add plugin` 粘贴试用包路径或 `.tgz` 安装，插件开关默认为 On |
| 首次使用要自己完成初始化、登录、选课、同步 | 在聊天里说“帮我设置 Moodle 学习助手”，按 6 步引导完成；每步有状态和错误提示 |
| 个人资料与程序在同一文件夹 | 程序在插件包内，个人资料由学生选择位置；升级/卸载插件不删资料 |
| 教学行为依赖项目根规则文件 | 插件自带教学技能与常驻教学规则，新用户无需配置 |
| 章节核对可能触发命令行授权流程 | 正常章节核对直接走 Harness 内的 Moodle 工具，同一聊天/章节复用回执 |
| 学习工具需要命令行 | 新增本地学习、设置与恢复工具；MCP 启动失败时仍有 Host 侧恢复入口 |

不变的部分：学校接口仍为**只读**（不提交作业、不代替作答、不发帖、不修改完成状态、不发邮件）；学校登录由学生本人在浏览器完成；密码、验证码和 Token 不进入聊天、日志或插件配置。

## 3. 安装与开始使用

面向学生的完整步骤见 [插件试用说明](PLUGIN_TRIAL_GUIDE.md)，这里只给最短路径：

1. DeepSeek Harness → 左侧 `Plugins` → `Add plugin` → 粘贴试用包文件夹绝对路径（或 `.tgz` 路径）→ 阅读第三方信任提示 → `Install`；
2. 确认插件开关为 **On**，新开一个聊天；
3. 说“帮我设置 Moodle 学习助手”，按提示完成资料位置、运行依赖、学校登录、课程与资料范围、首次同步；
4. 设置完成后即可说“讲解某章”“帮我复习某章”等开始学习。

> 桌面版 profile 由 Electron 独占管理，不能用命令行安装/管理插件；学生和验收都以 `Plugins` 页为准。

## 4. 升级方式

- 本试用包从本地路径或 `.tgz` 安装，插件页**没有** `Versions`/`Update`（那是 registry 来源才有的）；
- 升级 = 在 `Plugins` 页卸载旧版 → 安装新试用包 → 打开聊天按提示继续。卸载不会删除个人资料目录；
- 同一插件包已安装时直接重装会被拒绝，先卸载再装；
- 升级后如果 Moodle 工具没有出现，按提示重启一次 DeepSeek Harness，或让助手重新检查设置；工具重连后才能用新版本。

## 5. 回滚方式

- **回滚到未插件化的工作流**：在 `Plugins` 页卸载本插件即可。个人资料目录默认保留，数据布局（`Study/`、`config.local.json`、`.private/`）与项目工作区一致；如需继续用原项目方式，先备份资料目录，再把其中的数据文件放回项目工作区后按原方式启动。
- **回滚插件版本**：卸载当前试用包，重新安装之前的试用包（如持有）；个人资料目录不变。
- 回滚不会自动清理 `%LOCALAPPDATA%\MoodleStudyAssistant` 中的运行环境与日志；需要彻底清理时按试用说明第 7 节手动删除。
- 回滚前建议先备份整个资料文件夹；不要手工修改 `.private/` 或 `Study/_System/state.json`。

## 6. 验证范围

### 6.1 实际运行验证（本机、离线或本地）

- **构建与打包**：`tools/release/build-plugin.mjs` 成功生成 `dist/moodle-study-assistant/`（约 9.8 MB、2042 个文件），个人数据与敏感路径审计通过（包内不含 `Study/`、`.private/`、`config.local.json`、开发机绝对路径或姓名）；零 npm 安装依赖，随包 Node 运行时包可加载并注册工具。
- **真实桌面运行时安装/停用/启用/卸载**：在 DSH 桌面版当前 profile 中，通过插件管理器安装一个使用唯一行 id 与 `moodle_study_verify` 服务名、其余与发布包一致的验证副本，返回 `application: applied`；随后真实会话中出现 `moodle_study_chat`、`moodle_study_status`、`mcp__moodle_study_verify__study_setup_status` 等工具、`moodle-study` 技能与常驻教学规则，并实际调用了 `study_setup_location`、`study_setup_finish` 等工具（含失败路径）。停用 bundle 后这些工具、技能与规则消失；重新启用后恢复；卸载后 profile 的 `package.json` 恢复原样，支持目录与资料目录按设计保留。安装方式与桌面版 `Plugins → Add plugin` 使用同一插件管理器代码路径，但**没有用鼠标实际点选或截图**。
- **首次设置链路（隔离环境）**：MCP 握手、设置工具注册、资料位置选择、运行依赖真实安装（DSH 自带 Python 3.12 建立私有环境）、错误路径与状态重连提示，共 **22/22** 项检查通过。
- **学习工具**：study 模式下 **8/8** 工具注册通过，并完成真实本地引擎调用（`context`/`chapter-prep` 等）。
- **数据根分离**：`STUDY_DATA_ROOT` 覆盖回归 **node 177 + Python 103 + 学习工具 638** 项通过（未设置环境变量时保持旧行为）。
- **凭据与本地核对**：Windows DPAPI 加解密往返可用（凭据只绑定当前 Windows 用户）；真实本地 `chapter-prep` 在只读资料上可用。

### 6.2 仅代码/静态检查（未在真实界面或线上环境运行）

- 插件清单 `package.json` 的 `engines.dsh`（`^0.2.2-rc.1`）、`dsh.bundle.patch`、`platforms: ["win32"]`、`exports`/`files` 与显示元数据（`locale/en.json`、`locale/zh.json`、`icon.svg`）按当前 DSH 随包文档逐项核对，**未在真实插件页渲染验证**。
- `cordis.patch.yml` 的 `ctx.moodleStudy` 服务注入与 `!!js` 配置按 DSH 随包文档（并对 `dsh-mcp-client` 的 env 清洗规则）编写，MCP 子进程以 `ELECTRON_RUN_AS_NODE` 方式启动已在本地跑通，但**未在不同 DSH 版本上验证**。
- `docs/market/index.json` 已用 DSH 自带的 `@deepseek-ai/dsh-plugin-market-index` 校验器做结构校验（测试索引 URL）通过；**未**在启用了 Custom plugin indexes 的 DSH 里实际添加该索引或从 `Discover` 安装。
- 市场/发布事实（无官方索引、官方收录需随 DSH 发版、桌面 profile 不能用 CLI 管理）来自 DSH 随包文档与本机只读调研，未走官方收录申请。

### 6.3 未验证（需要本人操作或本阶段未执行）

- **真实学校账号登录与真实课程同步**：需要学生本人在学校页面完成 SSO/MFA；本次验证不包含真实登录、真实课程资料下载或学校公告/作业的线上采集。
- **桌面 UI 鼠标点击走查与截图**：安装对话框、第三方信任提示、安装结果、插件卡片与图标渲染、`Uninstall` 确认框未截图；插件管理器代码路径已按上节验证，但没在干净 profile 上从头用鼠标点击一遍。
- **端到端“首次设置 → 首次同步 → 讲一章”全流程**：各步骤工具与本地引擎已分别验证，但没有绕过登录后的完整真实链路验收。
- **npm 发布、市场索引托管、`Discover` 浏览安装、官方收录**：均未执行。
- **其他学校、其他电脑、其他 Windows 账户**：未验证；DPAPI 凭据与路径都绑定当前机器/用户。

## 7. 已知限制

- 仅支持 Windows（学校登录依赖 Windows 凭据保护和系统 Edge/Chrome）。
- 学校必须启用 Moodle Mobile Web Service，接口权限由学校控制，部分资料可能无法读取。
- 首次安装运行依赖约 20 MB 下载，首次连接学校还会安装登录组件（约 40 MB）；网络受限时会失败，恢复后可重试。
- DOCX/PPTX 无页图，只能读抽取文字；公式和图表应以 PDF 原页为准；扫描件 OCR 可能有误。
- 同一资料目录只对应一所学校一个账号；换学校/换账号需使用新的资料目录。
- 插件不执行任意命令，不下载授权范围外的内容，不提交作业或修改学校状态。
- 市场索引没有官方审核与签名；安装第三方插件即信任其代码，DSH 不验证索引声明的包内容。

## 8. 涉及的文件

- 插件源码：`plugin/`（`index.js`、`cordis.patch.yml`、`locale/`、`icon.svg`、`resources/`（教学规则、使用技能、Python 依赖清单）、`docs/`、`README.md`）
- 插件运行时与工具：`tools/moodle/plugin/`（设置、Python 运行时、任务、首次设置/恢复工具、学习工具）、`tools/moodle/mcp-entry.mjs`
- 构建：`tools/release/build-plugin.mjs`；产物 `dist/moodle-study-assistant/`（已加入 `.gitignore`，可由构建脚本重建，不随仓库提交）
- 回归测试：`tests/data-root.test.mjs`、`tests/test_data_root.py`，以及既有 Node/Python 测试套件
- 文档：本文件、[插件试用说明](PLUGIN_TRIAL_GUIDE.md)、[市场提交材料](market/README.md)、[市场条目文案](market/LISTING.md)
- 版本：插件包使用独立版本号 `0.6.0-trial.1`；仓库根 `package.json` 的公开版本仍为 `0.5.0`，只新增 `build:plugin` 脚本。
