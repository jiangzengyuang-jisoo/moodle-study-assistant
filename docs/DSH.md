# 在 DeepSeek Harness（DSH）中使用


本指南用于 `deepseek` 分支。安装只配置 DeepSeek Harness；学习引擎、状态协议与证据规则和 Codex 分支共用。本分支来自公开源码基线，已合入 D 盘至 6cdf2b1 的公开源码更新；新部署仍需实际验收。

## 1. 已实测环境

| 项目 | 实测值 |
| --- | --- |
| 客户端 | DeepSeek Harness 桌面版 `0.2.1-rc.5`（Windows） |
| 运行时 | 自带 Node `24.21.0`、Python `3.12.14`；系统 Node `24.19.0`、Python `3.12.15` 亦可 |
| 模型 | DeepSeek V4.1（DSH 中显示为 `deepseek-flash`），声明 `text`/`image` 输入；已实测 `read_image` 能读取原页 PNG/JPEG |
| 会话标识 | DSH 为每次 shell 调用注入 `DSH_SESSION_ID`；主聊天形如 `session-<uuid>`，内部子会话为裸 UUID |
| 规则加载 | 项目根 `AGENTS.md` 由 DSH 自动注入（`@deepseek-ai/dsh-agent-instructions`） |
| 技能 | DSH 默认扫描根不含项目 `skills/`；需按下文加入一次 `customSkillDirs`，重启后进入技能目录 |
| MCP | 通过 `@deepseek-ai/dsh-mcp-client`（stdio）托管本项目 `tools/moodle/run.mjs mcp`，只读工具 |
| 图片输入 | 单边 ≤ 8192px、≤ 20MiB、≤ 64MP；PNG/JPEG/WebP/GIF；超出会在 `read_image` 处明确报错 |

以上为 2026-11-02 在本机实测/代码核对结果。换机器、升级 DSH 或更换模型后需重新验证。

## 2. 一次性安装

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
- 真正新建聊天：新的 `DSH_SESSION_ID`，首次进入学习时正常采集一次。
- **禁止**随机生成 ID，也**禁止**所有聊天共用一个固定 ID；无法可靠取得身份时按上表第 2 行与本人约定名称，或明确报错停下。
- DSH 内部子会话/子代理拿到的是自己的裸 UUID，**不能**当原始聊天 ID：子任务必须由主助手把主聊天 ID 显式写进任务，并加 `--subtask`（只允许 `continue`/`status`，不联网、不写学习状态）。

日常命令（在项目根执行，未传 `--chat` 时自动使用上面的稳定身份）：

```powershell
.\study.cmd continue          # 学习接续；本聊天首次调用会完整采集一次
.\study.cmd today             # 接续 + 今日复习与视图
.\study.cmd status            # 只看本地状态，不联网
.\study.cmd refresh           # 本人明确要求更新学校信息时才用
```

## 4. 原页图片

- PDF 页：`tools/study/library.cmd render <material_id> --unit N` 返回 `path`（`Study/_System/Library/renders/<sha>/page-N-<dpi>.png`），随后让助手用 DSH `read_image` 读取该路径。默认 130dpi；密集公式/表格可用 144–180；本工具上限 300dpi。
- 图片原件：`render` 直接返回原文件路径，可直接 `read_image`。
- PPTX/DOCX：本工具不生成页图，只返回原文件与 slide/section 编号。DSH 中可用内置 `office-render` 技能渲成图片后再 `read_image`，或请本人提供 PDF/截图。
- 渲染结果 `verified:false`，只表示“看到原页”，不代表核读通过或掌握。

## 5. 只读与数据边界

- Moodle MCP 工具仍是只读查询集合；不注册提交、发帖、改完成状态等操作。选课时确认完整初始化后，刷新通过独立资料阶段自动下载、归档与索引授权课程的可访问内容；仅信息或未授权范围不下载。详见 [课程内容同步](COURSE_CONTENT.md)。
- 所有学习状态写入仍走 `study.cmd review` / `state_io.py` 等正式入口：锁、版本哈希、快照绑定、操作去重、历史证据保护不变；助手不直接改写 `state.json`。
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
