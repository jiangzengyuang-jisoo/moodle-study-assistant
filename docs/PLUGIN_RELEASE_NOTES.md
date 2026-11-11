# Moodle 学习助手插件试用版发布说明

- 发布对象：DeepSeek Harness 插件包 **`dsh-moodle-study@0.6.0-trial.4`**（当前准备包；前一准备版本 `0.6.0-trial.3` 未公开发布）
- 日期：2026-11-12（香港时间，`0.6.0-trial.4` 重新打包）；`0.6.0-trial.3` 准备版本为 2026-11-09；`0.6.0-trial.1` 为 2026-11-08
- 状态：**试用版**。尚未发布到 npm，也未进入任何市场索引；本说明描述的是仓库内的可安装产物，不表示已经公开发布或上架。`0.6.0-trial.4` 已重新打包，更早的准备包不包含本轮修复。
- 验证结论：本版修复的验证结论见 [CHANGELOG.md](../CHANGELOG.md) 的 2026-11-11 补漏条目；`0.6.0-trial.2` 的独立模拟学校验收为 **8 项要求场景全部 PASS（101/101 项检查）**，另附加回归 4/4 通过；真实学校登录、真实课程数据与桌面 UI 点击安装仍未验证（见 §2.3 与 §1）。
- 配套文档：[插件试用说明](PLUGIN_TRIAL_GUIDE.md) · [市场提交材料](market/README.md)

## 0. `0.6.0-trial.4`：插件恢复闭环补漏（2026-11-11 修复，2026-11-12 重新打包）

本版在 `0.6.0-trial.3` 基础上补上运行环境、登录恢复与安装器的几处漏判；不改变程序/数据分离布局、学校只读边界与安装方式。

- **运行依赖损坏真正重装**：重新调用 `study_setup_runtime` 时不再单独信任旧 `runtime.json` 成功标记。分级本地检查 venv 目录、解释器能否运行、pip 是否可用、`requirements-core.txt` 每个依赖能否导入，并比对依赖清单版本与 SHA-256 指纹；依赖文件存在但导入失败或指纹不符时真正重新安装（`--force-reinstall`），不会只补“缺失”后误报完成。`study_setup_status` 与 Host 侧 `moodle_study_status` 使用同一分级诊断。
- **重建 venv 后登录组件分级检查**：私有 venv 重建会顺带移除登录组件（Playwright），旧 `login.installed` 标记不能证明它仍可用。本版在连接学校前分级检查解释器可运行、`playwright.sync_api` 可导入、登录依赖指纹匹配；不匹配时重新安装登录组件，避免“已安装”标记与真实环境不一致。
- **安装器完整性（仓库源码/安装路径，不在插件包内分发）**：`dsh_integration.py install` 遇到同名 `moodle_study` MCP 配置重复、或既有生成段缺失/损坏时，不再静默选择或报告成功：重复条目判为 `conflict`、`--update-existing` 拒绝猜测；重建生成段后校验完整持久化文档（恰好一条指向当前项目的 MCP 条目 + 存在的 skills 目录）才写入，不会只验剥离后的副本而误报 `unchanged`；当前项目 `skills/` 目录缺失判为 `error` 且零写入。`setup.ps1` 在缺少完成助手时的回退分支也会复核 `status --json` 的 `configuration_ok`，配置未完成时不打印 Setup complete 并以非 0 退出。该脚本属于仓库源码安装流程，不随 Harness 插件包分发。
- **重登录后的连接与章节回执恢复**：保存的学校连接（配置 + DPAPI 凭据）带 generation 指纹；重登录进行中或刚完成时，旧连接与缓存 API 不会交给新请求，下一次只读调用最多用新凭据重试一次（保存内容未变时不自动重试的约定不变）。章节严格核对回执记录 `connection_generation`，冷启动进程也只在当前保存连接与回执一致时复用；登录或授权变化后旧回执失效，需要重新联网核对。
- **构建与本地包**：`node tools/release/build-plugin.mjs` 已重建 `dist/moodle-study-assistant/`（`0.6.0-trial.4`：3559 个文件、约 15.1 MB），个人数据与敏感路径审计通过；`pack:plugin` 已生成 `release/npm/dsh-moodle-study-0.6.0-trial.4.tgz`（3,190,497 字节 / 3558 个条目 / 92 个内置依赖包 / SHA-256 `50db3cb649bc7d56190411d91639a501d478f1d8e91bf33d1141c0a3ac775e87`），隔离解包后 `verify-plugin` **31/31** 通过（含真实 MCP 握手与真实联网安装核心 Python 依赖的本地验证）。
- **验证结论**：本版修复的完整验证结论见 [CHANGELOG.md](../CHANGELOG.md) 的 2026-11-11 补漏条目；本文件不重复全部测试数量与验收结论。
- **发布方式（准备中，尚未公开）**：计划由维护者确认后发布到 npm（包名 `dsh-moodle-study`，版本 `0.6.0-trial.4`，发布标签 `trial`，不设 latest），并用 GitHub Pages 托管社区插件目录；目录 URL 与 npm 页面尚未上线，学生暂时仍用本地试用包安装。
- 真实 Harness 重启、真实学校登录与课程同步仍需现场验收；未验证项与限制沿用 §2.3 及下文。

## 1. `0.6.0-trial.3`：完整资料同步也要求学校信息采集完整（2026-11-09，未公开发布，历史补漏记录）

本轮补上一个完成判定漏判；不改变程序/数据分离布局、安装方式与既有行为。

- **问题**：选择“完整资料同步”时，若课件下载成功但学校信息采集失败（例如作业/公告查询超时，采集记录明确 `collection_complete: false`），旧判定只检查资料同步报告，`study_setup_finish` 仍可能返回 `ready: true`。
- **修复**：`tools/moodle/plugin/sync-evidence.mjs` 的完整同步分支现在同时检查学校信息采集完整性——要求与当前账号、课程选择、授权范围匹配且完整的采集记录；最新采集记录不完整即判为 `partial`（不清除已下载资料），无采集记录、采集过期或账号不匹配也不会完成。
- **资料保留与增量恢复**：已下载的课件与资料同步报告保留不动；`study_setup_status` 逐门给出可用范围与 `collection_incomplete` 等剩余问题；重跑 `study_setup_sync` 增量补齐采集与资料（不重复下载已成功内容），之后 `study_setup_finish` 才返回 `ready`。
- **兼容已有记录**：资料同步报告中记录为完整采集的快照，在缺少刷新指针时仍作为采集证据复用，升级不会强迫已有用户全部重新同步。
- **回归测试**：`tests/plugin-recovery.test.mjs` 新增 3 项——“完整同步 + 采集不完整不完成且保留已下载资料”“完整同步报告无采集记录不完成”“已有完整采集记录可复用”；恢复测试 **19/19** 通过，全量离线回归 **node 196 + Python 105 + 学习工具 638**。
- **独立模拟学校聚焦复验 34/34 PASS**（隔离 fixture + 真实 MCP 工具调用；采集失败由 mock 让 `mod_quiz_get_quizzes_by_courses` 返回 500，走真实引擎采集路径）：先完成 full 同步（2 门课/17 份资料），再让采集失败后，`study_setup_status` 逐门返回 `partial` + `collection_incomplete`，`study_setup_finish` 返回 `SETUP_INCOMPLETE(sync_incomplete)`；已下载的 10 个资料文件路径与内容 SHA-256 与失败前完全一致（未删除/改写）；恢复采集后重跑同步 `already_current=10 / failed=0 / downloaded_bytes=0`（未重复下载），之后 `finish` 才返回 `ready`；`info_only` 行为无回归。
- 本次未使用真实学校账号；未验证项与限制沿用 §2.3 及下文。
- **发布方式（准备中，尚未公开）**：该准备版本未公开发布，已由 `0.6.0-trial.4` 取代（见 §0）。计划由维护者确认后发布到 npm（包名 `dsh-moodle-study`，当前版本 `0.6.0-trial.4`，发布标签 `trial`，不设 latest），并用 GitHub Pages 托管社区插件目录；目录 URL 与 npm 页面尚未上线，学生暂时仍用本地试用包安装。

## 2. 0.6.0-trial.2：设置恢复、断网学习与同步完成判定

本版修复试用反馈暴露的三个问题；不改变程序/数据分离布局、学校只读边界与安装方式。

### 2.1 修复内容

1. **设置与学习工具不再分两套，中断后可从当前进度继续。** MCP 每次启动都注册设置/恢复工具（`study_setup_status`、`study_setup_location`、`study_setup_runtime`、`study_setup_courses`、`study_setup_select_courses`、`study_setup_sync`、`study_setup_finish`、`study_connect_school`、`study_reconnect`、`study_job_status`）与本地学习工具，`next_steps` 引用的工具一定存在。任一步中断（关聊天、退出、断网、安装失败、同步失败）后重启，`study_setup_status` 会指出当前真正要做的下一步，不需要从头再来。已设置用户可在聊天里更换课程、更换资料位置、修复运行依赖；切换资料位置或登录成功后工具自动重连（约几秒）；旧目录资料原样保留，新位置不沿用旧目录的登录凭据，账号与课程授权检查保留。配套修复：Host 工具 `moodle_study_status` 在 MCP 工具不可用时也能区分运行依赖缺失、未登录与资料目录不可用。
2. **本地学习与远端连接解耦。** 学校网络故障时 `study_local_context`、`study_chapter_prep`、`study_material_read`、`study_material_render`、`study_teaching_event`、`study_local_status` 仍可用；联网工具返回结构化连接状态（`kind`、`next_step`、`requires_login`、`retryable`），网络故障用新增的 `study_reconnect` 重试，不要求重新登录；只有登录失效或凭据无法解密才需要学生本人重新 `study_connect_school`。章节核对失败会如实返回失败状态、不会写成成功回执，同一聊天/章节的回执复用规则与单次授权恢复规则不变。**不承诺离线模型推理**，只保证本机资料访问与本地教学记录。
3. **设置完成改为依据真实同步证据。** `study_setup_finish` 只有在学校采集快照 + 资料同步报告齐全且匹配当前账号、课程和授权范围时才返回 `ready`；空索引目录、同步未开始/失败/中断/部分/过期都会返回每门课程的可用范围与剩余问题，而不是“完成”。重跑 `study_setup_sync` 增量补齐、不重复下载已成功内容；`info_only` 与“课程确实无可下载资料”可以正常完成；升级前已存在且匹配当前账号与授权的有效记录直接复用。
4. **验收中发现并修复的附加问题**：章节核对工具在插件内可能只拿到懒连接网关、拿不到真实客户端，导致严格核对永远 incomplete（CRITICAL，已修复并回归）；未初始化时本地学习工具会直接透传原始 Python 异常（MINOR，现返回结构化 `LOCAL_STATE_MISSING` 提示并指向 `study_setup_status`）；干净运行时缺少可选的数学核验依赖 sympy 导致首次初始化失败（DEP，已改为容错，缺失时记 `null`，不影响聊天学习）。

### 2.2 用户可见变化

| 之前 | 现在 |
| --- | --- |
| 设置进行到一半退出或断网，工具集可能变化，需要从头再来 | 工具每次启动都注册；重启后 `study_setup_status` 从当前进度继续，下一步工具一定存在 |
| 设置完成后想换课程、换资料位置或修复依赖，缺少入口 | 在聊天里即可调整；切换位置保留旧目录资料，登录成功或切换位置后工具自动重连 |
| 学校网络故障时本地学习工具也可能一起不可用 | 本地学习工具照常可用；联网失败返回结构化状态，用 `study_reconnect` 重试且不需要重新登录 |
| 资料目录里只有空索引也可能被当作“设置完成” | 完成判定依据真实同步证据；空目录、失败、中断、部分、过期都会逐门说明问题 |
| 同步中断后重跑可能重复下载 | 重跑增量补齐，已成功内容不重复下载 |
| 断网时不清楚还能做什么 | 明确：本机资料可读、可继续教学记录；但联网信息不冒充最新，也不承诺离线模型 |

不变的部分：学校接口仍为**只读**（不提交作业、不代替作答、不发帖、不修改完成状态、不发邮件）；学校登录由学生本人在浏览器完成；密码、验证码和 Token 不进入聊天、日志或插件配置；程序在插件包内，个人资料在自选目录，升级或卸载默认保留。

### 2.3 验证范围

**实际运行验证（本机、离线隔离目录）**

- MCP 首用验证 **31/31** 项通过（`tools/release/verify-plugin.mjs`，隔离 support/data 目录，覆盖工具注册、资料位置、运行依赖真实安装、错误路径、自动重连与包内个人数据审计）。
- study 模式工具验证 **8/8** 项通过（`tools/release/verify-study-tools.mjs`，含真实本地引擎调用与工具注册一致性）。
- 恢复/完成判定单测 **19/19** 通过（`tests/plugin-recovery.test.mjs`：空目录不算完成、部分同步、过期证据、账号不匹配、`info_only`、完整同步但采集不完整、无采集记录、已有完整采集记录复用、工具注册与 `next_steps` 一致、`finish` 拒绝与完成、切换位置保留旧目录、网络与登录错误指引不同）。
- 插件包已按 `0.6.0-trial.3` 重建：本地安装包 **3557 个文件、约 15 MB**（含 MCP 服务器所需的完整 Node 运行时依赖闭包），npm 压缩包约 **3.0 MB**（3556 个条目）；全量离线回归 **node 196 + Python 105 + 学习工具 638** 项通过（含既有 177/103/638 项与本次新增的 19 项插件恢复测试、2 项运行时可选依赖测试）。
- 验证使用隔离的 support/data 目录，不读取真实 `Study/`、`.private/` 或学校凭据。
- 独立模拟学校验收（隔离 fixture 驱动真实 MCP 入口与真实本地引擎；修复前基线三缺陷均先复现）：最终一轮 **8 项要求场景全部 PASS（101/101 项检查）**，附加回归 D **4/4** 通过。关键结果：重启后 33 个工具全部注册且 `next_steps` 引用的工具真实可调用；依赖安装中断后 status 指回 `study_setup_runtime`，重跑跳过已完成的 venv 创建、只补装依赖后 `runtimeReady=true`；切换资料位置时旧目录 91 个文件 / 592,227 字节完全不变，新目录没有旧 token/state、联网工具返回结构化未连接、`finish` 不 ready，未初始化时本地工具返回结构化 `LOCAL_STATE_MISSING`；断网时工具集合 33=33 不变，本地学习工具全部可用（含真实本地 PDF 渲染 1 张图）、联网工具结构化失败、`study_reconnect` 可重试且不要求登录，本地回执复用 `reused:true/network_started:false`、`force` 重查返回 `STUDY_DNS` 不伪造成功；空 Library 时 `finish` 返回 `SETUP_INCOMPLETE(sync_incomplete)`，`http500` 返回结构化 `STUDY_HTTP`，部分失败逐门 `partial`、重跑后 `already_current=6/failed=0` 未重复下载、`finish` 才 ready；full 同步 17 份资料 / 17 个文本单元后 `finish` ready，info_only 0 下载、`_Inbox` 为空、无 `LibrarySync` 报告也可 `finish` ready；章节核对首次严格核 files=2/unchanged=2/error=0，同聊天复用、其他聊天不复用，授权不足与账号不匹配被拒绝，断网复用不联网。

**仅代码/静态检查（未在真实界面或线上环境运行）**

- 修复涉及的工具注册、`next_steps` 与实际工具名一致性、同步证据判定、位置切换后的重连调度与凭据不复制逻辑，由代码阅读与上述单测/验收覆盖；未在真实 Harness 插件页逐项点击验证。
- 文档中的工具名与行为按该版本工作区源码核对；`plugin/package.json` 与 `dist/` 的版本为 `0.6.0-trial.3`（该准备版本的历史记录；当前准备版本为 `0.6.0-trial.4`，见 §0）。

**未验证（需要本人操作或本阶段未执行）**

- 真实学校 SSO/MFA 登录与 `auth.py`/Playwright 浏览器登录路径（验收用 Windows DPAPI 加密的虚构 token 模拟已登录状态，未访问真实学校）。
- 真实学校数据与文件体量、权限受限内容与 `STUDY_ACCESS` 拒绝分支（验收用假学校响应与合成的真实字节文件）。
- DSH 桌面 UI 鼠标点击安装的完整走查与截图（`Plugins → Add plugin`、第三方信任提示、卸载确认）。
- 真实 MCP 客户端的自动重连机制（验收用“关闭并新建客户端”等价模拟）。
- 换 Windows 账户后 DPAPI 凭据无法解密的场景（只验证了凭据缺失时返回结构化错误）。
- npm 发布、市场索引托管、`Discover` 浏览安装、官方收录。
- 其他学校、其他电脑、其他 Windows 账户的安装表现。

### 2.4 升级方式与数据保留

- 在 `Plugins` 页卸载旧试用包（`0.6.0-trial.2` 或更早），再安装 `0.6.0-trial.3` 试用包；本地路径/tarball 来源没有 `Versions`/`Update`，同包名直接重装会被拒绝。
- 个人资料目录与运行支持目录默认保留，数据布局不变（`Study/`、`config.local.json`、`.private/`），无需迁移；已安装的运行依赖会被识别，通常不需要重装。
- 旧版设置做到一半时升级，重新打开聊天后会从当前进度继续；升级后如工具没有出现，重启一次 DeepSeek Harness 或让助手检查状态。
- 回滚：卸载新版、重新安装旧试用包；个人资料目录不变。

## 3. 0.6.0-trial.1 初始插件化改造（2026-11-08）

### 3.1 本次发布做了什么

本次把原来需要项目文件夹和命令行的 Moodle 学习助手，改造成一个可从 DeepSeek Harness 插件页安装的第三方 bundle：

1. **可安装的插件包**：包名 `dsh-moodle-study`，插件源码在 `plugin/`，构建脚本 `tools/release/build-plugin.mjs` 输出自包含的 `dist/moodle-study-assistant/`（该版构建约 **9.8 MB、2042 个文件**）。插件包零 npm 安装依赖，随包内置 6 个 Node 运行时包；Python 运行依赖由插件首次使用时安装。
2. **聊天内首次设置**：新增设置与恢复工具 `study_setup_status`、`study_setup_location`、`study_setup_runtime`、`study_connect_school`、`study_setup_courses`、`study_setup_select_courses`、`study_setup_sync`、`study_setup_finish`、`study_job_status`，把「选资料位置 → 装运行依赖 → 本人登录学校 → 选课程与资料范围 → 首次同步 → 自动重连」串成有状态的引导流程，失败可重试、不覆盖已有资料。
3. **教学能力接入插件**：注册 `self-study-tutor` 教学技能、`moodle-study` 使用技能，以及常驻教学规则（一次讲透一个适量知识点、可渲染 LaTeX、指定章节不扩大范围、回复末尾不堆技术日志）。没有项目根 `AGENTS.md` 的新用户也会得到这套行为。
4. **章节核对走 MCP 工具**：`study_check_chapter` 保持严格内容哈希核对；同一聊天、同一章节且本地文件未变时复用回执、不联网，本地新增本章资料只核对新增范围；只有学生明确要求才强制重查。异常只在工具未加载或 MCP 启动/传输故障时走 CLI 后备，受限解密错误每操作最多申请一次单次授权。
5. **本地学习工具（无需 shell）**：`study_local_context`、`study_chapter_prep`、`study_material_read`、`study_material_render`、`study_teaching_event`、`study_local_status`、`study_refresh`、`study_job_status`；Host 侧另有 `moodle_study_chat`（取当前聊天标识）和 `moodle_study_status`（MCP 启动失败时仍可读取设置状态并给出恢复步骤）。
6. **程序与个人数据分离**：程序在插件包内；学习资料、配置和 Windows DPAPI 加密的学校凭据放在学生选择的资料目录（布局仍是 `<资料目录>/Study/`）；运行环境、日志和设置状态放在 `%LOCALAPPDATA%\MoodleStudyAssistant`。升级或卸载插件默认保留个人资料。
7. **引擎支持独立数据根**：新增 `STUDY_DATA_ROOT` 环境变量覆盖（未设置时行为与旧版一致），Node/Python 引擎按程序根定位自身文件、按数据根读写学生数据；仓库新增对应回归测试 `tests/data-root.test.mjs`、`tests/test_data_root.py`。

### 3.2 用户可见变化

| 之前 | 现在 |
| --- | --- |
| 需要项目文件夹、安装步骤和多条命令，安装与启动门槛较高 | 在 DSH 插件页 `Add plugin` 粘贴试用包路径或 `.tgz` 安装，插件开关默认为 On |
| 首次使用要自己完成初始化、登录、选课、同步 | 在聊天里说“帮我设置 Moodle 学习助手”，按 6 步引导完成；每步有状态和错误提示 |
| 个人资料与程序在同一文件夹 | 程序在插件包内，个人资料由学生选择位置；升级/卸载插件不删资料 |
| 教学行为依赖项目根规则文件 | 插件自带教学技能与常驻教学规则，新用户无需配置 |
| 章节核对可能触发命令行授权流程 | 正常章节核对直接走 Harness 内的 Moodle 工具，同一聊天/章节复用回执 |
| 学习工具需要命令行 | 新增本地学习、设置与恢复工具；MCP 启动失败时仍有 Host 侧恢复入口 |

不变的部分：学校接口仍为**只读**（不提交作业、不代替作答、不发帖、不修改完成状态、不发邮件）；学校登录由学生本人在浏览器完成；密码、验证码和 Token 不进入聊天、日志或插件配置。

### 3.3 安装与开始使用

面向学生的完整步骤见 [插件试用说明](PLUGIN_TRIAL_GUIDE.md)，这里只给最短路径：

1. DeepSeek Harness → 左侧 `Plugins` → `Add plugin` → 粘贴试用包文件夹绝对路径（或 `.tgz` 路径）→ 阅读第三方信任提示 → `Install`；
2. 确认插件开关为 **On**，新开一个聊天；
3. 说“帮我设置 Moodle 学习助手”，按提示完成资料位置、运行依赖、学校登录、课程与资料范围、首次同步；
4. 设置完成后即可说“讲解某章”“帮我复习某章”等开始学习。

> 桌面版 profile 由 Electron 独占管理，不能用命令行安装/管理插件；学生和验收都以 `Plugins` 页为准。

### 3.4 升级方式

- 本试用包从本地路径或 `.tgz` 安装，插件页**没有** `Versions`/`Update`（那是 registry 来源才有的）；
- 升级 = 在 `Plugins` 页卸载旧版 → 安装新试用包 → 打开聊天按提示继续。卸载不会删除个人资料目录；
- 同一插件包已安装时直接重装会被拒绝，先卸载再装；
- 升级后如果 Moodle 工具没有出现，按提示重启一次 DeepSeek Harness，或让助手重新检查设置；工具重连后才能用新版本。

### 3.5 回滚方式

- **回滚到未插件化的工作流**：在 `Plugins` 页卸载本插件即可。个人资料目录默认保留，数据布局（`Study/`、`config.local.json`、`.private/`）与项目工作区一致；如需继续用原项目方式，先备份资料目录，再把其中的数据文件放回项目工作区后按原方式启动。
- **回滚插件版本**：卸载当前试用包，重新安装之前的试用包（如持有）；个人资料目录不变。
- 回滚不会自动清理 `%LOCALAPPDATA%\MoodleStudyAssistant` 中的运行环境与日志；需要彻底清理时按试用说明第 7 节手动删除。
- 回滚前建议先备份整个资料文件夹；不要手工修改 `.private/` 或 `Study/_System/state.json`。

### 3.6 验证范围

#### 3.6.1 实际运行验证（本机、离线或本地）

- **构建与打包**：`tools/release/build-plugin.mjs` 成功生成 `dist/moodle-study-assistant/`（该版约 9.8 MB、2042 个文件），个人数据与敏感路径审计通过（包内不含 `Study/`、`.private/`、`config.local.json`、开发机绝对路径或姓名）；零 npm 安装依赖，随包 Node 运行时包可加载并注册工具。
- **真实桌面运行时安装/停用/启用/卸载**：在 DSH 桌面版当前 profile 中，通过插件管理器安装一个使用唯一行 id 与 `moodle_study_verify` 服务名、其余与发布包一致的验证副本，返回 `application: applied`；随后真实会话中出现 `moodle_study_chat`、`moodle_study_status`、`mcp__moodle_study_verify__study_setup_status` 等工具、`moodle-study` 技能与常驻教学规则，并实际调用了 `study_setup_location`、`study_setup_finish` 等工具（含失败路径）。停用 bundle 后这些工具、技能与规则消失；重新启用后恢复；卸载后 profile 的 `package.json` 恢复原样，支持目录与资料目录按设计保留。安装方式与桌面版 `Plugins → Add plugin` 使用同一插件管理器代码路径，但**没有用鼠标实际点选或截图**。
- **首次设置链路（隔离环境）**：MCP 握手、设置工具注册、资料位置选择、运行依赖真实安装（DSH 自带 Python 3.12 建立私有环境）、错误路径与状态重连提示，共 **22/22** 项检查通过。
- **学习工具**：study 模式下 **8/8** 工具注册通过，并完成真实本地引擎调用（`context`/`chapter-prep` 等）。
- **数据根分离**：`STUDY_DATA_ROOT` 覆盖回归 **node 177 + Python 103 + 学习工具 638** 项通过（未设置环境变量时保持旧行为）。
- **凭据与本地核对**：Windows DPAPI 加解密往返可用（凭据只绑定当前 Windows 用户）；真实本地 `chapter-prep` 在只读资料上可用。

#### 3.6.2 仅代码/静态检查（未在真实界面或线上环境运行）

- 插件清单 `package.json` 的 `engines.dsh`（`^0.2.2-rc.1`）、`dsh.bundle.patch`、`platforms: ["win32"]`、`exports`/`files` 与显示元数据（`locale/en.json`、`locale/zh.json`、`icon.svg`）按当前 DSH 随包文档逐项核对，**未在真实插件页渲染验证**。
- `cordis.patch.yml` 的 `ctx.moodleStudy` 服务注入与 `!!js` 配置按 DSH 随包文档（并对 `dsh-mcp-client` 的 env 清洗规则）编写，MCP 子进程以 `ELECTRON_RUN_AS_NODE` 方式启动已在本地跑通，但**未在不同 DSH 版本上验证**。
- `docs/market/index.json` 已用 DSH 自带的 `@deepseek-ai/dsh-plugin-market-index` 校验器做结构校验（测试索引 URL）通过；**未**在启用了 Custom plugin indexes 的 DSH 里实际添加该索引或从 `Discover` 安装。
- 市场/发布事实（无官方索引、官方收录需随 DSH 发版、桌面 profile 不能用 CLI 管理）来自 DSH 随包文档与本机只读调研，未走官方收录申请。

#### 3.6.3 未验证（需要本人操作或本阶段未执行）

- **真实学校账号登录与真实课程同步**：需要学生本人在学校页面完成 SSO/MFA；本次验证不包含真实登录、真实课程资料下载或学校公告/作业的线上采集。
- **桌面 UI 鼠标点击走查与截图**：安装对话框、第三方信任提示、安装结果、插件卡片与图标渲染、`Uninstall` 确认框未截图；插件管理器代码路径已按上节验证，但没在干净 profile 上从头用鼠标点击一遍。
- **端到端“首次设置 → 首次同步 → 讲一章”全流程**：各步骤工具与本地引擎已分别验证，但没有绕过登录后的完整真实链路验收。
- **npm 发布、市场索引托管、`Discover` 浏览安装、官方收录**：均未执行。
- **其他学校、其他电脑、其他 Windows 账户**：未验证；DPAPI 凭据与路径都绑定当前机器/用户。

### 3.7 已知限制

- 仅支持 Windows（学校登录依赖 Windows 凭据保护和系统 Edge/Chrome）。
- 学校必须启用 Moodle Mobile Web Service，接口权限由学校控制，部分资料可能无法读取。
- 首次安装运行依赖约 20 MB 下载，首次连接学校还会安装登录组件（约 40 MB）；网络受限时会失败，恢复后可重试。
- DOCX/PPTX 无页图，只能读抽取文字；公式和图表应以 PDF 原页为准；扫描件 OCR 可能有误。
- 同一资料目录只对应一所学校一个账号；换学校/换账号需使用新的资料目录。
- 插件不执行任意命令，不下载授权范围外的内容，不提交作业或修改学校状态。
- 市场索引没有官方审核与签名；安装第三方插件即信任其代码，DSH 不验证索引声明的包内容。

### 3.8 涉及的文件

- 插件源码：`plugin/`（`index.js`、`cordis.patch.yml`、`locale/`、`icon.svg`、`resources/`（教学规则、使用技能、Python 依赖清单）、`docs/`、`README.md`）
- 插件运行时与工具：`tools/moodle/plugin/`（设置、Python 运行时、任务、首次设置/恢复工具、学习工具）、`tools/moodle/mcp-entry.mjs`
- 构建：`tools/release/build-plugin.mjs`；产物 `dist/moodle-study-assistant/`（已加入 `.gitignore`，可由构建脚本重建，不随仓库提交）
- 回归测试：`tests/data-root.test.mjs`、`tests/test_data_root.py`，以及既有 Node/Python 测试套件
- 文档：本文件、[插件试用说明](PLUGIN_TRIAL_GUIDE.md)、[市场提交材料](market/README.md)、[市场条目文案](market/LISTING.md)
- 版本：`0.6.0-trial.1` 时插件包使用独立版本号，仓库根 `package.json` 的公开版本仍为 `0.5.0`，只新增 `build:plugin` 脚本。

`0.6.0-trial.2` 在本节基础上新增或修改：`tools/moodle/plugin/remote.mjs`、`tools/moodle/plugin/sync-evidence.mjs`、`tests/plugin-recovery.test.mjs`，以及 `tools/moodle/mcp-entry.mjs`、`tools/moodle/plugin/{setup-tools,onboarding-tools,study-tools,settings}.mjs`、`plugin/index.js`、`tools/study/review.py`、`tools/release/verify-plugin.mjs`、`tools/release/verify-study-tools.mjs`。

`0.6.0-trial.3` 在本节基础上补漏：修改 `tools/moodle/plugin/sync-evidence.mjs`（完整同步的采集完整性检查）、`tests/plugin-recovery.test.mjs`（3 项回归测试）、`plugin/resources/skills/moodle-study.md` 与相关文档版本号；未改动安装方式、程序/数据布局与既有工具行为。

`0.6.0-trial.4` 在上述基础上修复恢复闭环：`tools/moodle/plugin/python-runtime.mjs`（分级运行环境/登录组件健康检查、损坏依赖重装 `--force-reinstall`）、`tools/moodle/plugin/remote.mjs`（重登录连接代际与缓存失效）、`tools/moodle/chapter-check.mjs` 与 `tools/moodle/source-gateway.mjs`（回执/连接 generation 绑定与重登录后单次重试）、`tools/moodle/plugin/{onboarding-tools,settings}.mjs`、`plugin/index.js`（Host 侧分级探测）、`tools/onboarding/dsh_integration.py` 与 `tools/onboarding/setup-dsh.ps1`（安装器完整性）及相关测试；`dist/` 已按 `0.6.0-trial.4` 重新构建，未改动安装方式与程序/数据布局。
