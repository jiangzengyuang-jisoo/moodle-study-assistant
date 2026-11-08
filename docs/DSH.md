# 在 DeepSeek Harness（DSH）中使用


本指南用于 `deepseek` 分支。安装只配置 DeepSeek Harness；学习引擎、状态协议与证据规则和 Codex 分支共用。本分支来自公开源码基线，已合入 D 盘至 6cdf2b1 的公开源码更新；新部署仍需实际验收。

## 1. 已实测环境

| 项目 | 实测值 |
| --- | --- |
| 客户端 | DeepSeek Harness 桌面版 `0.2.2-rc.1`（Windows；Electron `44.7.0`、Chromium `152.0.7977.130`） |
| 运行时 | 自带 Node `24.21.0`、Python `3.12.14`；系统 Node `24.19.0`、Python `3.12.15` 亦可 |
| 模型 | DeepSeek V4.1（DSH 中显示为 `deepseek-flash`），声明 `text`/`image` 输入；已实测 `read_image` 能读取原页 PNG/JPEG |
| 会话标识 | DSH 为每次 shell 调用注入 `DSH_SESSION_ID`；主聊天形如 `session-<uuid>`，内部子会话为裸 UUID |
| 规则加载 | 项目根 `AGENTS.md` 由 DSH 自动注入（`@deepseek-ai/dsh-agent-instructions`） |
| 技能 | DSH 默认扫描根不含项目 `skills/`；需按下文加入一次 `customSkillDirs`，重启后进入技能目录 |
| MCP | 通过 `@deepseek-ai/dsh-mcp-client`（stdio）托管本项目 `tools/moodle/run.mjs mcp`，只读工具 |
| 图片输入 | 单边 ≤ 8192px、≤ 20MiB、≤ 64MP；PNG/JPEG/WebP/GIF；超出会在 `read_image` 处明确报错 |

以上为 2026-11-02 在本机实测/代码核对结果；客户端版本与运行时已于 2026-11-08 重新核实（当前安装 `0.2.2-rc.1`）。换机器、升级 DSH 或更换模型后需重新验证。

## 2. 一次性安装

默认在本项目的 Harness 聊天中说“帮我安装并教我怎么用”，助手按 [CHAT_ONBOARDING.md](CHAT_ONBOARDING.md)运行无交互步骤、打开学校网页、在聊天列课程让本人选择，完成后主动教学。以下命令主要供助手执行和排错，不能把另开 CMD 或菜单操作当作必经步骤。

前置：Node ≥ 22、Python ≥ 3.11；先在本项目目录运行过 `setup.cmd`（创建 `.venv`、`node_modules`、空白 `Study/`，已有学习档案会保留）。

### 2.1 合并 DSH 配置

在 **DSH 聊天里**运行（推荐，会自动识别 DSH 环境）：

```powershell
& '.venv/Scripts/python.exe' tools/onboarding/dsh_integration.py install
```

或在普通终端手动运行同一命令（DSH 已启动过、存在 `~/.dsh/profiles/desktop` 即可）。该命令：

- 用 PyYAML 解析现有 profile patch（`setup` 会安装该依赖），按**实际配置结构**处理：注释、注释掉的示例都不算配置；支持现有配置中的 `!!js` 表达式；顶层合法的空列表 `[]` 会被正确归一化，不会拼出无效 YAML；
- 只追加两段条目：MCP stdio 客户端 + 技能目录 `customSkillDirs`；
- 保留用户已有配置的每一行，写入前生成 `cordis.patch.yml.bak-<UTC时间>` 备份；
- 用 `# >>> ... >>>` / `# <<< ... <<<` 标记自己的段落，**重复运行只更新本段**（幂等）；
- 写入前把合并结果完整解析并校验（顶层结构、条目内容、路径指向），任何解析或校验失败都**保留原文件不替换**；
- 若已存在同名 `moodle_study` MCP 或用户自定义 `skill-filesystem` 覆盖，默认**不覆盖**。确认旧条目是本项目的旧部署时，可只在原文件中更新该条目的 `args` 路径（支持流式列表与跨行折叠路径；其他字段、注释和其他条目保持原样）。即使技能目录已经配置好、无需新增条目，这项修改也会实际写入并复核后才报告成功；`--dry-run` 只显示内容，不写文件：

```powershell
& '.venv/Scripts/python.exe' tools/onboarding/dsh_integration.py install --update-existing
```

- 不读取、不写入任何学习数据、凭据或 Cookie。

本分支运行 `setup.cmd` 或 `setup.ps1` 时都会按 `client-target.json` 执行上述合并；无需先在 Harness 内运行。合并失败会停止并报告未完成，不冒充安装成功。

<details>
<summary>手动配置示例（脚本不可用时）</summary>

把 [`templates/dsh/cordis.patch.example.yml`](../templates/dsh/cordis.patch.example.yml) 中的 `<PROJECT_ROOT>` 换成项目绝对路径（正斜杠），追加到 `~/.dsh/profiles/desktop/cordis.patch.yml` 末尾。不要删除或重排文件中已有条目。

</details>

### 2.2 重启并打开工作区

1. 完全退出并重新启动 DeepSeek Harness（profile 配置在启动时加载）。
2. 新建聊天时选择 **本项目文件夹**（含 `AGENTS.md`、`Study/`）作为工作区。DSH 的写入权限只覆盖会话工作区，因此后续真实学习请以这个目录为工作区；不要把“公开源码目录”和“学习部署目录”混用。
3. 新聊天中可先问：“你用到了哪些项目规则和教学技能？”——正常回答应能说明已读取 `AGENTS.md`，并能加载 `self-study-tutor` 技能。

### 2.3 诊断

```powershell
# 本地诊断（不联网、不写学习记录、不碰凭据），JSON 便于核对
.\diagnose.cmd -Json -NoPause

# 只看 DSH 接线状态（不会修改任何文件）
& '.venv/Scripts/python.exe' tools/onboarding/dsh_integration.py status --json

# 只看聊天身份解析结果（不写任何文件）
& '.venv/Scripts/python.exe' tools/study/chat_identity.py
```

诊断中 `dsh`/`client` 的分工：

- `dsh` 行只报告**配置结构**：MCP 条目是否指向本项目的 `tools/moodle/run.mjs`、技能目录是否加入 `customSkillDirs`、现有配置是否冲突/无法解析；`status --json` 里的 `configuration_ok` 表示配置正确，`runtime_loaded` 固定为 `unverified`——**配置检测不等于运行成功**。
- `client` 行只表示当前 DeepSeek 分支的 DSH profile 配置可用，同样不代表运行时已加载；是否真正加载需重启后在新聊天中实际确认。
- 若 profile patch 无法解析或缺少 YAML 依赖，诊断只给 warning 并明确“未修改任何配置”，不会报成“配置正确”。
- 已有同名 MCP 冲突、技能目录未加入、profile 无法解析时，按 `status --json` 的 `warnings` 与上文手动处理。

## 3. 聊天身份规则（与 Codex 一致）

`study.cmd` / `session.py` 解析原始聊天 ID 的顺序：

| 顺序 | 来源 | 说明 |
| --- | --- | --- |
| 1 | 显式 `--chat "原始聊天ID"` | Codex 主助手继续显式传入；DSH 也可显式传 |
| 2 | 环境变量 `STUDY_CHAT_ID` | 客户端没有稳定 ID 时，与本人约定一个会话名并一直沿用（可恢复的替代方式） |
| 3 | DSH 的 `DSH_SESSION_ID` | **仅接受主聊天**的 `session-<uuid>` 形式，自动接续 |

规则：

- 同一聊天跨轮、重开、上下文压缩：ID 不变，第二次起复用已核验结果，**不会重复首次刷新**。
- 真正新建聊天：新的 `DSH_SESSION_ID`；需要校务信息时首次完整采集一次，开始教某一章只做 `chapter-check`。
- **禁止**随机生成 ID，也**禁止**所有聊天共用一个固定 ID；无法可靠取得身份时按上表第 2 行与本人约定名称，或明确报错停下。
- DSH 内部子会话/子代理拿到的是自己的裸 UUID，**不能**当原始聊天 ID：子任务必须由主助手把主聊天 ID 显式写进任务，并加 `--subtask`（只允许 `continue`/`status`，不联网、不写学习状态）。

日常命令（在项目根执行，未传 `--chat` 时自动使用上面的稳定身份）：

```powershell
.\study.cmd continue          # 需要校务信息的学习接续；本聊天首次调用会完整采集一次
.\study.cmd today             # 接续 + 今日复习与视图
.\study.cmd status            # 只看本地状态，不联网
.\study.cmd chapter-check --course 课程ID --section "Topic 5" --mode strict   # 只核对本章文件，不完整采集
.\study.cmd chapter-check --course 课程ID --section "Topic 5" --mode strict --force  # 本人明确要求重新核对
.\study.cmd teaching-event    # 讲解后保存教学位置（stdin 小 JSON；见 SESSION.md）
.\study.cmd refresh           # 本人明确要求更新学校信息时才用
```

教或复习某一章前，可先用只读批量准备（不联网、不写学习状态）：

```powershell
& 'tools/study/library.cmd' chapter-prep --course 课程ID --section "Topic 6"
& 'tools/study/library.cmd' render <material_id> --units 34,35,36
```

`chapter-prep` 一次返回本章材料/unit 清单、headings 目录、预算内整页正文、公式图表候选页（复用哈希渲染缓存）和本章接续；它的 `strict_check.last_receipt` 只是上次回执背景，**不是本轮核验**，本章文件仍须 `chapter-check --mode strict` 核对。

### 慢速章节核对与后台任务

- `chapter-check` 的 stderr 是简短阶段进度，stdout 只有最终 JSON；不要用 `Select-Object -Last 40` 等截断进度，也不要在前台等待时反复启动同一条命令。
- 本机 DSH 实测支持 shell 后台任务：`run_in_background` 立即返回 job id，运行中可用 `job_output` 读取已产生的 stdout/stderr（`[status: running]`）；等待时继续跟进**原任务**即可，不重复启动，不另起第二个核对。
- 完成通知不是收尾保证：后台任务在 DSH 进程内运行，会话被处置、DSH 退出或通知竞态都可能让通知/结果不可靠；最终结果应在给出答复前用 `job_output`（必要时 `wait: true`）汇总，不能承诺“最终回复后自动后台收尾”。
- 同聊天同范围且本地文件未变时使用上次严格回执（`reused: true`、`network_started: false`），通常数秒内返回且不联网；只有真实首次核对、资料变化、换聊天/账号或 `--force` 才需要较长联网时间。
- 进度输出不代表核对完成；最终以 stdout 的 JSON 回执 `verdict`、`verified.complete` 和 `receipt_path` 为准。

### 工具并行、Auto review 与后台任务边界（2026-11-08 核实）

- **工具并行**：DSH 支持同一 assistant 消息内多个工具调用，但只有声明为并发安全的工具（本版包括 `read`、`read_image`、`web_search`/`web_fetch`、`subagent` 等）会并行执行，本机默认上限 10 个在飞；`pwsh`、`grep`、`glob`、`skill`、`job_*`、写文件工具、MCP 工具等是**独占屏障**，同一消息里的多个 `pwsh` 不会并行。准备阶段（`tools/pre-execute` 中的策略/审批）按模型顺序串行，只有工具体重叠，结果按模型顺序提交。独立长命令要并发，请用 `run_in_background` 启动、后续用 `job_output` 收集；不要把“同一消息多个 `pwsh`”当作并行加速。
- **实验性 Auto review**：客户端带实验性 `auto` 权限预设（`/permission auto`），选中后每次工具调用会额外发起一次审批模型请求，本机实测约 **+2 秒/调用**。它不是项目默认，本文也不建议为教学任务修改全局权限配置来启用；教学等耗时敏感任务优先减少工具调用次数或在工具侧批量化（一次读多页、一次核对多文件），这比提高并发上限更有效。
- **后台任务边界**：job 记录与输出环是**进程内**的，DSH 退出或会话被处置后不会继续或恢复；单个 owner 的后台作业默认最多 10 个；完成通知在“忙”时注入下一步、“空闲”时默认以 follow-up turn 唤醒，但存在 retirement-window 竞态，pending 通知也不随会话处置保留。后台任务只用于同一聊天内的慢操作跟进，不能作为收尾的唯一依赖；需要结果时在最终答复前用 `job_output` 汇总，不承诺“最终回复后在后台自动跑完并自动收尾”。

### 授权解密失败时申请单次重试

当当前已授权的学校只读操作在 Harness 受限执行环境中返回 `STUDY_TOKEN_DECRYPT_FAILED`，主助手应主动发起下面的单次授权流程；不要等本人再复制提示词，也不要先另问一句“要不要重试”。这是一项申请权限的规则，不是预先批准执行。

1. 保留刚失败的原始命令、工作目录、参数、课程/章节/资料范围和原聊天 ID，确认原任务已经结束。若原调用已用 `danger-full-access` 仍失败，不再次申请；本规则不适用于未授权任务、学校权限拒绝或账号范围不符。
2. 用 Harness 的 `pwsh` 工具重新提交同一命令，设置 `sandbox_permissions: "danger-full-access"`，同时提供中文 `justification`，例如“需要在正常 Windows 用户环境中使用已有加密授权，重试刚才失败的本章只读核对”。由客户端弹出单次审批，只有获得 `allowed-once` 后才执行。不得自行启动提权进程、改全局权限或绕过审批。
3. 每个失败操作最多申请并获批重试一次。拒绝、取消、审批不可用或重试仍失败时停止；同聊天同一操作的普通追问、继续学习、压缩或重开不重置次数、不再次弹申请，除非本人明确要求重试。保留失败事实，本地课件够用时继续教学；需要本人处理时只简述必要动作，不反复附连接日志。
4. 严格沿用原范围；不要把 `chapter-check` 改成 `continue/refresh`，不要附带其他命令、主动加 `--force`、删除回执或更换聊天 ID 来强制联网。若原命令因上次失败记录只返回旧状态，不能把它当作恢复成功；停止并按现有恢复规则处理，不扩大这次授权。
5. 看实际结果：连接检查成功只表示接口可连接；章节核对仍须实际成功回执才算核实。成功后接着原任务，不要求本人重复发起教学。不自动重新登录、重置/复制/打印凭据或修改学习记录；正常项目工具可在本机进程内使用加密授权，助手不得直接读取 `.private/`。

此流程是“失败不自动重试”的窄例外：助手主动**申请**一次，运行仍由客户端逐次批准，命令本身不添加自动重试。它也不属于扩大连接维修。其他错误维持原恢复流程；一次失败不能推断学校授权过期。

当前 Harness `0.2.2-rc.1` 的静态实现已确认上述 `pwsh` 参数和 `allowed-once` 门槛。Windows 受限执行可能阻止 PowerShell 子进程的输出管道，本项目会把这类子进程失败归入解密错误；不一定已经执行到真正的解密步骤。这里不承诺每次授权后必然成功，也不将普通终端连接成功当成 Harness 内的真实验收。

## 4. 原页图片

- PDF 页：`tools/study/library.cmd render <material_id> --unit N` 返回 `path`（`Study/_System/Library/renders/<sha>/page-N-<dpi>.png`），随后让助手用 DSH `read_image` 读取该路径。默认 130dpi；密集公式/表格可用 144–180；本工具上限 300dpi。
- 图片原件：`render` 直接返回原文件路径，可直接 `read_image`。
- PPTX/DOCX：本工具不生成页图，只返回原文件与 slide/section 编号。DSH 中可用内置 `office-render` 技能渲成图片后再 `read_image`，或请本人提供 PDF/截图。
- 渲染结果 `verified:false`，只表示“看到原页”，不代表核读通过或掌握。

## 5. 只读与数据边界

- Moodle MCP 工具仍是只读查询集合；不注册提交、发帖、改完成状态等操作。选课时确认完整初始化后，刷新通过独立资料阶段自动下载、归档与索引授权课程的可访问内容；仅信息或未授权范围不下载。详见 [课程内容同步](COURSE_CONTENT.md)。
- 所有学习状态写入仍走正式入口：纯教学位置用 `study.cmd teaching-event`（只交本轮变化，程序合并、校验、原子保存并至多重建一次视图），完整核读用 `study.cmd review`，其他状态用 `state_io.py` 等既有入口；锁、版本哈希、快照绑定、操作去重、历史证据保护不变；助手不直接改写 `state.json`。
- 普通问答、查询或纠正本地记忆不触发 Moodle 联网；只有学习启动或本人明确刷新才检查学校信息。
- DSH 配置里只有本机路径和 `serverName`，不含凭据；Moodle Token 仍由 Windows DPAPI 加密保存在 `.private/`，不进入聊天、状态或 Git。

## 6. 测试

离线回归（需先在项目内完成 `setup`；建议在真实终端运行）：

```powershell
node --test tests/*.test.mjs
& '.venv/Scripts/python.exe' -B -m unittest discover -s tests -p 'test_*.py'
& '.venv/Scripts/python.exe' -B -m unittest discover -s tools/study -p 'test_*.py'
```

DSH 适配新增的测试：

- `tests/test_dsh_integration.py`：聊天身份解析、DSH profile 合并/备份/幂等/冲突保护、CLI 行为；
- `tests/chat-identity.test.mjs`：Node 侧身份解析（`refresh` 入口）；
- `tests/test_onboarding.py`：按发行目标诊断，避免另一客户端配置掩盖本端缺失。

注意：在 DSH 的受限 shell 里直接运行 `node --test` 或 Python `tempfile` 相关测试可能被沙箱拒绝（Windows 管道/临时目录权限），这不代表代码失败；请在普通终端运行完整回归。

## 7. 已知限制与待验证

- 技能目录与 MCP 配置修改后**必须重启 DSH** 才生效；运行中的聊天不会热加载 profile。
- 在聊天中人工上传图片→模型识别的链路尚未在本适配中实测（`read_image` 读取本地文件已实测）。
- DSH 跨桌面版重启后同一聊天 ID 是否保持，尚未实测；以 `chat_identity.py` 输出与第二次 `continue` 是否复用为准。
- 真实学校 SSO/MFA、Windows OCR 中文语言包、PPTX/DOCX 经 `office-render` 的清晰度仍需按实际课件验证。
- 如果 DSH 默认模型不是支持图片输入的 DeepSeek 模型，`read_image` 会明确拒绝；请在校内设置中确认模型（本版按 DeepSeek V4.1 验证）。
- 在 Windows 受限环境（如本仓库之外的沙箱）运行测试命令可能失败，属于执行环境限制，不是学习工具回归。

课程资料库初始化和增量同步见 [COURSE_LIBRARY.md](COURSE_LIBRARY.md)。
