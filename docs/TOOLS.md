# 工具入口与验证

以下命令在用户的学习项目根目录运行。普通同学通过自然语言提出任务；本页供 AI 和维护者参考。不要在共享技能安装目录运行个人初始化或保存学习状态。

## Moodle

```powershell
node tools/moodle/run.mjs init
node tools/moodle/run.mjs check
node tools/moodle/run.mjs courses
node tools/moodle/run.mjs library-sync
node tools/moodle/run.mjs chapter-check --course 课程ID --section "Topic 5" --mode strict [--force]
node tools/moodle/run.mjs refresh
node tools/moodle/run.mjs mcp
```

`init` 只创建缺失的配置/空白状态，不覆盖已有资料。`courses` 交互选择跟踪页面，并确认课程级资料授权（完整初始化或只同步课程信息），保留未选页面的既有历史；完整初始化、非交互入口和授权保存位置见 [课程级资料授权与自动同步](COURSE_LIBRARY.md)。`library-sync` 对已保存完整授权的课程页面增量枚举、下载、归档、登记并建立索引；学校接口仍只用只读调用，不开始测验尝试、不提交作业、不发帖、不修改学校状态。`chapter-check` 先用本地资料索引与来源映射定位本章模块，再只比对本章文件（默认 `--mode strict` 内容哈希，`quick` 只看元数据）；Moodle 区块位置、日期和 Tutorial 编号不会仅因数字相同被选中，匹配不唯一时返回候选和原因，不写状态、不装文件、不建索引，回执在 `Study/_System/ChapterChecks/`，记录学校/账号归属、已定位模块和本地文件内容哈希；同聊天同范围严格回执可按聊天摘要复用、本地新增资料只核对新增部分，`--force` 强制重查。判定见 [单章资料核对](WORKFLOW.md#单章资料核对不触发完整采集)。`refresh` 采集到 `Study/_System/Runtime/runs/<run_id>/snapshot.json`，完整成功另存 `refresh-last-complete.json`；不直接更新个人掌握、作业完成或 AI 核读状态。退出码2表示部分采集失败。一个 `Study/` 档案只对应一所学校和账号：选课、旧 CLI 授权、资料同步、采集、下载、刷新预检、章节核对和登录会先核对档案已记录的学校/账号，不一致时停止且不写入；换学校请复制/新建独立项目目录并使用独立档案，见 [课程级资料授权](COURSE_LIBRARY.md#一档一校一账号)。

AI 在需要校务信息或用户明确要求时调用 `continue/refresh`，不会每条追问重复执行；单章教学优先用 MCP `study_check_chapter`（`chapter-check` 为异常 CLI 后备），不会触发完整采集。命令本身始终进行真实采集。采集器全局最多同时执行4个 API 请求，每个论坛的分页仍按顺序读取，完整正文、回复和权限核对保留。同聊天复用返回的是上次严格回执（标记 `reused: true`、`network_started: false`），不是新的联网检查；本人明确要求重新核对时，工具用 `force: true`（CLI 后备用 `--force`）；不为获取新结果而更换聊天 ID 或扩大成完整刷新。CLI 后备 `chapter-check` 的阶段进度在 stderr，最终结构化结果只在 stdout；慢速首次核对可由助手用 Harness 后台任务跟进同一任务，不用 `Select-Object -Last 40` 截断进度。不要把没有重新联网的缓存复用记为新的检查。

MCP 暴露原有 15 个课程、作业、测验、论坛、日历、成绩、通知、资源目录和站点信息读取工具，并新增 `study_check_chapter`（项目服务合计 16 个工具）；全论坛分页用 `study_all_discussions`，回复用 `study_discussion_posts`。上游 recent discussions 只有最近20条，不能替代完整采集。不注册学校写操作；普通查询不下载文件，章节核对只读取授权文件字节作哈希比对并保存本地回执，不安装资料。正常刷新在采集后为有课程级完整授权的范围自动下载、归档和索引；没有授权的范围只同步信息。选课授权、结果和重试入口见 [COURSE_CONTENT.md](COURSE_CONTENT.md)。

`moodle_get_calendar_events` 支持课程、时间范围和分页限制，先看 `complete` 再判断是否覆盖所选事件；分页不前进或到页数上限返回 `complete:false`，应缩小时间范围继续查，不能把部分结果当成全部。`moodle_get_notifications` 用 `limit/offset`、`has_more`/`next_offset` 续读，不把通知标为已读。成绩只查当前账号；资源目录只列可见 resource/folder/url 模块元数据，不下载、不扩大授权；站点信息只返回必要身份与能力字段并脱敏凭据。接口未启用时返回 `api_not_enabled`，不等于没有数据。完整性只描述所选接口和时间范围，不等于所有类型的学校事件，也不等于 AI 已核读。升级后需重启本项目 MCP 连接。范围与限制见[更新报告](UPDATE_REPORT_2026-11-07-deepseek.md#按需只读学校查询)。

### 章节核对工具（study_check_chapter）

正常章节教学在本地 `context --read-only` / `chapter-prep` 后，调用已连接 Moodle 服务的 `study_check_chapter`；不用先运行 shell，也不在工具成功后再运行 `strict_check.command`。

```json
{"course_id":"COURSE101","section":"Topic 5","chat_id":"原始用户聊天ID"}
```

| 参数 | 含义 |
| --- | --- |
| `course_id` | 本地课程 ID；必须对应当前所选、已授权课程范围 |
| `section` | 明确章节名或编号，如 `Topic 5`；按本地资料索引与来源映射解析 |
| `module_ids` | 可选，来自本章已确认来源的模块编号数组；不能猜 ID 或借此扩大课程范围 |
| `chat_id` | 必填的原始主聊天 ID；与现有学习接续一致，不用内部子会话 ID、服务 ID、随机值或全局固定值 |
| `force` | 可选，默认 false；仅本人明确要求重新核对时为 true |

`section` 或 `module_ids` 至少一项；常规只选其一，同时传时按明确模块核对，章节名只作标签。严格核对固定为 SHA-256 内容比对，不提供 quick 模式。上述 JSON 是结构示例，实际参数须取本轮真实课程与主聊天 ID，不能直接传占位文字。工作目录、状态读取、学校连接和回执位置由服务端固定，调用者不能传命令、文件路径、学校 URL 或凭据。工具会读取已授权学校文件字节并保存本地 `Study/_System/ChapterChecks/` 回执；不安装/替换资料、不改学习状态、不提交作业或修改学校数据。核对不等于核读或掌握。

返回 `STUDY_MCP_RESTART_REQUIRED` 时先重新加载 Moodle 服务，不申请 shell 提权；学校/账号/授权/范围等固定错误按其原因处理，不能把缓存失败或普通错误当作核对成功。

回执沿用现有 `verdict`、`verified.complete`、`scope`、`reused`、`network_started` 等含义。回执记录保存的学校连接指纹（`connection_generation`：配置与加密凭据的哈希）；相同原聊天、当前保存的学校连接、课程/资料范围及本地内容未变时才可复用严格回执，重新登录、换账号或凭据变化后旧回执失效、需要重新联网核对。新增范围只补核对新增资料，错误和未完成不能冒充成功。MCP 服务不保证继承主聊天身份，所以每次明确提供 `chat_id`；只在本地保存其摘要。

工具缺失/未加载或服务启动、传输故障才退回同范围 `study.cmd --chat "原始用户聊天ID" chapter-check --course COURSE101 --section "Topic 5" --mode strict`；不因学校拒绝、账号/课程授权不符、参数错误或客户端审批拒绝而换路径。不并发运行或在两条路径间循环；仅后备 CLI 遇到受限执行解密错误才申请一次单次授权，详见 [DSH 恢复流程](DSH.md#章节核对默认使用-moodle-工具)。用户自定义审批策略仍然生效，不修改全局权限。

更新后重连本项目 MCP 服务（无单独入口则重启 Harness）加载新工具；已有配置和学习资料不迁移，助手不擅自重启用户客户端。`chapter-prep` 输出的 `strict_check.command` 仅用于上述后备流程，不能据其存在强制正常教学走 shell。

### 课程级授权与资料初始化

在选课入口 `courses` 中，命令先列出课程，再让本人选择页面并确认“课程级资料授权”模式：`1` 完整初始化（同步课程信息 + 下载/归档/建立索引）、`2` 只同步课程信息（不下载）、`0` 取消。确认后授权保存到 `state.settings.course_library_authorization`；之后日常同步沿用授权增量补齐，新增课程页面须重新授权。完整初始化若本机有 6 小时内完整快照则直接复用，否则先跑只读 `refresh`，再执行资料同步；采集与核读仍分离，下载/解析成功不等于已核读或已掌握。详细范围、归档、学科合并和缺口规则见 [COURSE_LIBRARY.md](COURSE_LIBRARY.md)。

非交互入口仅供 AI 在用户当前对话已明确授权后使用，`--yes` 表示用户已确认该课程范围，不是程序自行授权：

```powershell
# 完整初始化；只同步信息时把 full 换成 info_only
node tools/moodle/run.mjs courses --select 146265,142648 --mode full --yes
# 可选参数：--refresh 忽略 6 小时快照重新采集；--no-sync 只保存授权；
# --chat 原始聊天ID 把原始用户聊天 ID 传给采集（子任务用主聊天 ID 并加 --subtask）
```

日常增量同步：

```powershell
node tools/moodle/run.mjs library-sync [--course 课程ID ...] [--snapshot 路径] [--no-snapshot] [--max-bytes N] [--batch-size 1-20] [--dry-run]
```

`--course` 使用 state 课程 ID（如 `moodle-146265`），可重复；`--snapshot` 必须位于 `Study/` 内，省略时优先使用最近 30 分钟内的完整快照，没有完整快照则用只读 API 现场读取，`--no-snapshot` 强制使用只读 API；`--max-bytes` 默认 64 MiB；`--batch-size` 1–20，默认 8；`--dry-run` 只统计不下载。逐项状态写入 `Study/_System/LibrarySync/ledger.json`，最近报告和缺口清单为同目录的 `latest.json`、`资料缺口.md`。中途停止后再次运行只补未完成/新增/变更项，失败项自动重试。

### 明确授权的资料下载

已保存课程级完整授权的课程页面由资料同步自动处理，不需要逐份下载。用户明确请求授权范围以外或点名单份“某章课件/某份老师说明”时，先从所选课程的实际模块列表识别对应资源，再调用独立入口：

```powershell
node tools/moodle/run.mjs download 123 --authorized
```

`123` 只是示例模块编号，不能照抄；多个模块必须逐个明确列出。`--authorized` 表示助手已经收到用户对这些资料的明确下载请求，不是程序替用户授权。仅限当前所选课程的 resource/folder 模块；不扩大成课程级授权范围外的抓取，不通过输入任意 URL 获取其他文件。

下载先落到 `Study/_Inbox/`，保留官方文件名，按内容哈希去重，同名不同内容保留为不同文件。成功下载不代表已经核读或掌握。AI 随后检查实际文件、课程归属和页数，按用户目录约定归档并更新 `materials`；下载器本身不替 AI 作语义判断。账号、权限或站点限制导致失败时如实说明，不绕过限制。

## 一次准备一章（chapter-prep）

```powershell
& 'tools/study/library.cmd' chapter-prep --course COURSE101 --section "Topic 6" [--modules 501,502] [--chat "原始用户聊天ID"] [--text auto|full|outline|none] [--text-budget 60000] [--render auto|none|always] [--render-limit 8] [--dpi 130] [--include-solutions] [--include-archived] [--read-only]
```

`chapter-prep` 只读，一次返回教或复习一章所需材料，代替“搜索一页—读一页—再决定”的逐页往返：

- **章节定位**：`resolution`（status/basis/intent/module_ids/candidates/reason）。先用本地资料索引与来源映射定位本章模块；匹配不唯一或没有本地资料时 `status` 为 `ambiguous` / `no_materials`，返回候选、原因和本地章节提示，不按数字相同扩大范围；`--modules` 直接按模块编号定位。
- **材料清单**：`materials` 给出 material id、标题、相对路径、`unit_kind`、`units`、`text_units`、`unit_range`、模块/班级区块和源哈希；`strict_scope` 说明 strict 核对会覆盖的模块与文件（文件夹模块可能比按文件匹配范围更宽）。
- **目录**：`outline.materials[].headings/page_map`；heading 是抽取导航，不是已核读证明，空 heading 不代表无内容。
- **整页正文**：`text.materials[].pages`（每页 `page/heading/text/flags`）。默认 `--text auto` 在 `--text-budget` 内按整页返回（默认 60000 字符，上限 400000）；超出部分进入 `text.omitted`，附 `library.cmd read <material_id> --start … --end …` 补读命令。`--text outline` 只给目录，`--text none` 不返回正文，`--text full` 上限 400000 字符。
- **公式图表候选页**：`visuals.candidates`（`unit/why/priority/verified:false`）与 `visuals.rendered`（`path/cached/verified:false`）。`--render auto` 只渲染优先级最高的 `--render-limit` 页（默认 8，最多 64），`--render none` 不渲染，`--render always` 渲染全部候选；PDF 页按源哈希复用 `Study/_System/Library/renders/` 缓存，`cache.render_reused/render_created` 区分复用与新建。把 `rendered[].path` 交给 `read_image` 查看原页；PPTX/DOCX 会返回 `rendered:false` 与 `instruction`，不会伪装成已视觉验证的页图。
- **本章接续**：`continuity` 给当前教学位置、本章待答题题干（隐藏答案/评分）和本科本章相关待复习项；不返回作业、考试或其他任务集合（省略原因写在 `continuity.omitted`）。
- **上次 strict 回执**：`strict_check.last_receipt` 只是**上一次**严格核对的摘要（`found/verified_complete/chat_key_matches/note`），**不是本轮核验**；`strict_check.required` 恒为 true，`strict_check.preferred` 为 `mcp`，`strict_check.tool` 给出 `study_check_chapter` 的名称与已知参数；若 `missing_arguments` 含 `chat_id`，先补本轮真实主聊天 ID，不传占位符。`strict_check.command_role` 为 `fallback_only`，`strict_check.command` 仅给出异常 CLI 后备的 `chapter-check` 命令，成功后不执行第二次核对。传入 `--chat` 只影响回执的聊天键匹配，不会联网或改写回执。
- **覆盖与只读边界**：`coverage`（`units_total/units_text/units_solutions_hidden/visual_review_required/verified:false`）是待核对清单，不是覆盖结论；`limits` 的 `network/state_written/maintenance/sync/ocr` 均为 false，`--read-only` 为兼容标志。命令不写学习状态、不写核读回执、不联网、不建索引；只可能新建 `Library/renders/` 下的派生页图。与其它 library 命令一样，它会写一行不含题目/答案/聊天 ID 的本地耗时日志。源文件或缓存不新鲜时标 `needs_sync`，不返回旧文本，先按 `cache.sync_command` 同步/重建再准备。
- **仍要做的核对**：公式、图表、表格、题号必须看原页；`coverage.verified` 恒为 false，整章覆盖仍需逐 unit 核对文字或原页。准备资料不等于核读或掌握。

## 批量渲染（render --units）

```powershell
& 'tools/study/library.cmd' render COURSE101-LECTURE1 --units 34,35,36 [--dpi 130]
```

一次校验来源后批量渲染/复用多个 unit，输出 `{material_id, verified:false, renders:[{unit, path, cached, …}]}`；`--unit` 单页用法继续兼容。`render` 只写 `Study/_System/Library/renders/` 下的派生页图，不写学习状态。`chapter-prep --render auto` 已按候选页批量渲染，通常不必再手动逐页 render。

## 复习、复测、反馈和当前计划

资料初始化和快速读取另见 [LIBRARY.md](LIBRARY.md)：`tools/study/library.cmd context` 直接读取最新状态，`sync` 构建或更新资料缓存，`chapter-prep` 一次准备一章，`search/read/render/outline` 用于定向补读与核对原页。课程资料的自动初始化、增量同步与缺口见 [COURSE_LIBRARY.md](COURSE_LIBRARY.md)。这些命令不写学习进度、作答或 FSRS。

```powershell
& '.venv/Scripts/python.exe' 'tools/study/daily.py' run
& '.venv/Scripts/python.exe' 'tools/study/daily.py' check
```

默认状态为 `Study/_System/state.json`。`--state 路径` 和带时区的 `--at 时间` 放在子命令前，仅测试/迁移需要；不把演示日期或测试作答写进正式状态。

- `run` 重建当前计划并选择核验过的复习新题。首次默认预算15分钟；以后省略 `--minutes` 沿用当天预算，明确减量才传更小值，包括0。预算计入原学习时间，不因重复运行叠加。新档案无题库时显示待准备。
- `render` 只重建当前状态视图，不创建题包或作答。
- `check` 检查学习时区的日期、状态版本、9份当前系统页面、已登记试卷与模拟题错题区；失败后重读状态并重新运行。不启动后台刷新。
- 队列 `review_queue` 项通过 `mistake_id` 关联真实问题，具体题在 `practice_variants`。每题需有 id、完整 prompt、非空字符串列表 `answer_check`、`estimated_minutes`、材料依据 `source` 和 `verified: true`。`source` 包含 `material_id`、`pages` 与 `detail`。AI 必须实际核验答案；不能只把标志改成 true。答案只供批改，不提前出现在今日待答题单。
- 复习选择题沿用考试题库的 `options` 协议：`practice_variants` 中的选择题变式带 `options`，即“选项键 → 选项文本”的字典，键的插入顺序就是展示顺序；至少两项，键和文本都须为非空字符串。`daily.py run` 生成日包时把核验过的选项深拷贝为题目自己的 `options` 快照，`prepare` 公开预览和 `Today Review.md` 会按原顺序逐行显示 `A. 文本`，作答前仍不显示 `answer_check`、答案或反馈；非待答题目（已讲解、已作答等）不公开选项。已生成日包中的选项快照按原样沿用，题库变式之后被编辑也不改写。旧日包若没有有效选项快照、而题库中的对应变式已是选择题，不从题库补猜：原题保留为历史并标为 `needs_review`、`budget_exempt: true`，记录 `options_review_reason` 与 `options_review_required_at`；该条不再占用当天预算，可另选未使用的新变式，没有备用变式时在 `needs_preparation` 说明。这类未补全选项的旧选择题只能作历史或辅助记录，独立 FSRS 评级会被拒绝。

### 保存作答

将 JSON 通过标准输入传给 `daily.py attempt`。必填字段如下；编号取自当前题包，以下是字段说明，不能作为本人作答录入：

| 字段 | 内容 |
| --- | --- |
| attempt_id | 本次真实尝试唯一编号，同一次重试复用 |
| session_id / review_id / variant_id | 当前题包中的三个关联编号 |
| answer / feedback / evidence | 本人原答、针对原答的核对、真实对话或文件页定位 |
| independent / answer_seen_before_attempt | 是否独立、作答前是否看过提示/答案，均为布尔值 |
| rating | 仅独立尝试填写 again / hard / good / easy |
| reviewed_at / actual_minutes | 可选真实带时区时间和用时；用时未知为 null |

`again` 是未独立答出或实质错误；`hard` 是独立答对但困难；`good` 是独立答对；`easy` 还需要本人明确反馈轻松。讲解后的答案用 `independent: false`，不填 rating。重复记录去重，冲突拒绝覆盖；未答不评级，晚交答案仍保留原题关联。FSRS 只安排间隔，不修改整门课掌握程度。

展示提示或解答时，以 `daily.py answer-shown` 记录 `session_id`、`review_id`、`variant_id` 和实际 `evidence`，可选 `shown_at`。这是曝光记录，不是虚构一次作答；下一次独立检查换新变式。

旧 `review.py record` 已退役，所有档案（包括缺少学习闭环启用标志的旧档案）均拒绝直接写入，并提示改用 `daily.py attempt`。`review.py due --minutes 15` 使用当前选题、来源与曝光校验，返回 `read_only_preview`，与正式题包共用当天剩余容量规则且不保存预览题包；无核验新题时返回待准备，不再展示旧 prompt。`review.py render` 转到 `daily.py render` 统一页面入口，仍返回 `status: rendered`；FSRS 内部核心与保存函数保留供当前流程调用。不要绕过 `daily.py` 直接调用内部函数给真实状态评级。

页面内容未变时保留原文件与修改时间，新鲜度检查复用同轮计划内容；这是减少重复文件操作和计划计算，不代表测得整体显著提速。状态保存后页面失败仍可沿用原 `attempt_id` / `operation_id` 重试，不重复追加作答或 FSRS 历史。

### 保存学习反馈

通过标准输入调用 `daily.py feedback`。必填 `feedback_id`、`completed`、`evidence`；可选 `date`、`recorded_at`、`course_id`、`task_ids`、`actual_minutes`、`blocker`、`next_step`。

`time_scope: session` 表示明确互不重叠的一次学习段；`cumulative` 表示当天累计时间；不明确则省略或为 `unknown`。未知范围不求和，最近一次累计报告不再叠加每题复习时间。自报完成不等于学校提交或独立掌握。一次用时不会自动训练排程参数。

当天有明确总预算和可靠用时反馈时，主计划、正式复习题包（`daily.py` 的 `run/attempt/feedback/answer-shown/mock-attempt` 准备）和 `review.py due` 只读预览共用剩余容量：`planning.remaining_study_minutes` 按档案学习时区，用当天已知总预算（容量折算的学习预算，以及计划中直接记录的总时长；课程范围预算不当作当天总容量）减去可靠用时反馈；反馈按最近一次 `cumulative` 或明确互不重叠且编号唯一的 `session` 合计，逐题复习用时已计入累计反馈时不再重复扣除。容量耗尽后停止新增题，未开始题标为 `deferred` 且 `budget_exempt`，保留已作答、已曝光和 FSRS 历史，不记失败；重复运行或切换课程只沿用当天额度，不增加；未知用时范围或未知总容量返回空值，不猜测。

所有写入均以最新状态为基础，保留上一版本、验证临时 JSON、检查并发冲突后保存。状态写入必须顺序执行，不能并行提交多个命令。

## 保存教学进度（teaching-save）

`teaching-save` 是新教学进度与原答的统一保存入口。实际讲解、收到并反馈真实作答、切换主题或收尾后，先读最新 `revision`，再把 UTF-8 JSON 交给标准输入；事件以 `courses[].teaching_events` 追加保存，共享锁内完整校验、原子保存后回读确认。相同编号同内容重试不重复记录，同编号换内容或过期 `expected_revision` 拒绝覆盖；写入或回读失败不会返回已保存。该命令不改写旧原答、评分、掌握或 FSRS，也不重建页面视图。

```powershell
Get-Content -Raw -Encoding UTF8 'tmp/teaching-save.json' | .\tools\study\library.cmd teaching-save --course 课程ID
```

字段、实际发生时间、聊天/轮次来源、原答与帮助条件、待答题状态及回执说明见 [MEMORY.md](MEMORY.md#教学接续保存与漏记检查)。总览、单科 `context` 与 `memory` 共用同一教学位置读取口径；需要更新页面时再按原流程运行 `daily.py run` 与 `daily.py check`。

## 保存教学位置（teaching-event，兼容旧流程）

`teaching-event` 继续兼容旧流程（引导登记、旧档案、`checks_append`/`review_backlog_append` 等），新教学进度与原答优先用上面的 `teaching-save`。讲完一章、推进或切换教学位置后，通过标准输入提交一个小事件；程序读最新状态合并、校验、原子保存并至多重建一次视图，成功后不必再跑 `daily.py run/check`：

```powershell
Get-Content -Raw -Encoding UTF8 'tmp/teaching-event.json' | .\study.cmd --chat "原始用户聊天ID" teaching-event [--operation-id 编号]
```

事件结构示例（值必须来自本轮实际教学，不能照抄）：

```json
{
  "course_id": "moodle-100000",
  "fields": {
    "chapter": "Topic 6: Aggregate Production",
    "topic": "总量生产函数与增长来源",
    "stage": "first_explanation_delivered_pending_check",
    "next_step": "等本人回答理解检查",
    "material_id": "moodle-100000-SOURCE-501-<哈希>",
    "pages": [1, 2, 3],
    "pending_check": {"id": "topic6-check-1", "prompt": "为什么人口增长能提高总产出却通常不能提高人均产出？", "status": "unanswered"},
    "coverage_note": "已讲主内容；作业要求未核对"
  },
  "onboarding": "shown"
}
```

事件只描述本轮变化（完整说明见 [SESSION.md](SESSION.md#教学位置事件teaching-event)）：

| 顶层键 | 必填 | 内容 |
| --- | --- | --- |
| `course_id` | 是 | state 中已登记的课程 ID |
| `chat_id` | 否 | 由入口注入；显式提供时必须与当前聊天一致，否则拒绝 |
| `event_id` | 否 | 事件自带编号（可选）；需要固定重试编号时用 `--operation-id`，不要依赖手填编号 |
| `fields` | 否* | 教学位置与描述性记录，见下表 |
| `checks_append` | 否* | 追加已完成记录，最多 5 条；只追加，相同记录自动去重 |
| `review_backlog_append` | 否* | 追加旧章待复习项，最多 3 条；键 id/topic/chapter/status/next_step，status 目前只接受 pending |
| `archive_pending` | 否 | true 时必须同时提供 `fields.pending_check` |
| `onboarding` | 否* | `shown` 或 `skipped`，仅在已实际展示后登记 |

\* 至少要提供 `fields / checks_append / review_backlog_append / onboarding` 之一，否则报“没有可应用的变化”。

`fields` 允许的键：

| 键 | 内容 |
| --- | --- |
| `chapter` / `topic` | 当前章节与主题；与旧值不同即视为换章，程序自动把旧位置快照追加到 `history`（只追加一次，不递归复制 checks/history） |
| `stage` / `next_step` | 教学阶段与下一步 |
| `material_id` / `pages` | 本章资料 ID（须已在 state.materials 登记且属于本课程）与已讲页码（正整数数组；`null` 表示清空） |
| `pending_check` | 待答题；`null` 表示清空。不能携带实际作答或评分；旧题已有实际作答时必须先用 `checks_append` 带上该完整记录，或传 `archive_pending: true` |
| `source` | 来源证据。同章内只能追加新键，已有值不能改写；换章时可整体替换（旧来源随旧位置存入 `history`） |
| `user_request` / `lesson_scope` / `worked_example` / `coverage_note` | 描述性课程记录，可创建或更新；不是掌握证据 |

输出为一行 JSON：`status`（`recorded` / `unchanged` / `duplicate`）、`course_id`、`operation_id`、`revision`、`state_written`、`views_updated`、`changed_fields`、`pending_check_id`、`history_appended`、`onboarding_status`、`created_cursor`、`pending_archived`、`mastery_updated: false`。

- 无变化：合并后与最新状态相同 → `unchanged`，不写盘、不重建视图。
- 重试：省略 `--operation-id` 时编号由“聊天 + 事件内容”派生，同内容重试得到同编号 → `duplicate`，只补视图；需要固定编号时先用 `study.cmd --chat "ID" operation-id` 分配，再用 `--operation-id` 提交，重试沿用原编号和内容；同编号不同内容报冲突。
- 视图生成崩溃：学习状态已提交时重试只补视图，不重复保存事件。
- 保存成功后可选用 `daily.py check` 只读确认页面；教学位置之外的状态（任务、错题、复习队列、计划、考试、跨日呈现等）仍走各自命令并 `daily.py run/check`。
- 禁止：掌握度/评级、改写既有 `checks/history`、证据集合（`topic_evidence`/`evidence_bindings` 等）、考试与作业记录、其他课程、未知键、超长超大事件。内部任务（`--subtask`）不能写教学事件；事件里的聊天 ID 必须来自当前入口，不手填。

## 考前覆盖与模拟卷

```powershell
# 将 <run_id> 替换为本轮入口返回的运行编号，使用返回的 snapshot_path。
& '.venv/Scripts/python.exe' 'tools/study/daily.py' exam-sync --snapshot 'Study/_System/Runtime/runs/<run_id>/snapshot.json'
& '.venv/Scripts/python.exe' 'tools/study/daily.py' exam-intake --snapshot 'Study/_System/Runtime/runs/<run_id>/snapshot.json'
```

采集候选不是确认官方要求。`exam-profile` 登记实际核读的要求，`mock-register` 登记 AI 已核验的原创卷，`mock-attempt` 保存真实作答，`mock-answers` / `mock-answer-shown` 记录答案/提示曝光。输入结构、三种试卷模式和来源变更处理见 [EXAM_PREP.md](EXAM_PREP.md)。这些命令不会自动阅读附件、编题或检查证明。

## 数学核验

向 `.venv/Scripts/python.exe tools/study/math_check.py` 的标准输入传入 JSON，例如：

```json
{"operation":"limit","expression":"(x**2-1)/(x-1)","variable":"x","point":"1"}
```

支持 simplify、evaluate、differentiate、integrate、limit、equivalent。用 `**` 表示乘方；默认符号为实数，可用 `symbols: {"x": "positive"}` 指定正数条件。equivalent 需提供 other；evaluate 可提供 values。

默认双侧极限会分别检查左右侧。化简相等仅指共同定义域，不自动证明两式定义域相同。只接受白名单数学表达式，不用 eval；这不是不受信任输入的资源隔离沙箱，特别复杂的符号运算仍可能耗时。保留题设，结果不替代证明审查；会计借贷和经济模型条件也需 AI 按老师方法核对。

## DeepSeek Harness（DSH）

```powershell
# 只读状态：profile 位置、MCP/技能是否已指向当前项目、聊天身份类型
& '.venv/Scripts/python.exe' tools/onboarding/dsh_integration.py status --json

# 合并 DSH profile 配置：只追加 MCP 客户端与 customSkillDirs，保留已有配置并写备份
& '.venv/Scripts/python.exe' tools/onboarding/dsh_integration.py install

# 查看聊天身份解析结果（不写任何文件）
& '.venv/Scripts/python.exe' tools/study/chat_identity.py
```

`install` 以**实际 YAML 结构**处理 profile patch（依赖 PyYAML，由 setup 安装）：注释不算配置，合法的空列表 `[]` 会被正常归一化；写入前完整解析并校验合并结果，解析或校验失败时保留原文件不替换；用 `# >>> ... >>>` 标记段幂等更新，重复运行只改本段。`install` 返回 `installed`/`unchanged`/`dry-run`/`conflict`/`skipped`/`error` 状态，并带同时覆盖 MCP 与技能目录的 `configuration_ok`、分项 `mcp`/`skills`、`warnings` 与可操作 `next_steps`；退出码 0 只用于“成功且两部分都配置正确”（`installed`/`unchanged`/`dry-run` 且 `configuration_ok=true`）。同名 `moodle_study` MCP 指向旧项目、出现多条同名条目、或用户自定义 `skill-filesystem` 覆盖缺当前项目 `skills/` 目录时返回 `conflict`：不写入任何内容、原文件不变、退出非 0；当前项目 `skills/` 目录本身缺失时返回 `error`（同样零写入），不会生成指向不存在目录的配置。确认旧条目是本项目旧部署时，可用 `install --update-existing` 只更新该条目的 `run.mjs` 路径（同名条目不止一条时拒绝猜测，按提示手动去重）；技能冲突按 [DSH.md](DSH.md) 合并或移除旧覆盖；不要删除现有配置重试。已存在的生成配置段会重建并校验完整持久化文档后才写入（恰好一条指向当前项目的 MCP 条目 + 存在的技能目录），不会因剥离后的副本看似正确而误报 `unchanged` 或完成。配置正确时重复运行返回 `unchanged` 并退出 0。`status --json`/`install --json` 的 `configuration_ok` 只表示配置结构正确（同时覆盖 MCP 与技能目录），`runtime_loaded` 恒为 `unverified`——配置检测不等于运行成功，必须重启 DSH 后在新聊天实际确认。修改后需重启 DSH 才加载。`study.cmd`/`session.py` 的 `--chat` 可省略：按“显式 `--chat` → 环境变量 `STUDY_CHAT_ID`（与本人约定的会话名）→ DSH 主聊天 `DSH_SESSION_ID`”解析；DSH 内部子会话的裸 UUID 会被拒绝，子任务必须显式传主聊天 ID 并加 `--subtask`。完整说明见 [DSH.md](DSH.md)。

在受限 shell（例如 DSH 自己的沙箱）里，`node --test`、部分 Python `tempfile` 测试，以及需要 Node 子进程、DPAPI 解密或网络下载的 `courses` 完整初始化和 `library-sync`，可能因管道/临时目录/进程权限失败；这不代表实现错误。助手应先按 Harness 实际提供的审批机制请求执行同一具体操作，并在当前聊天继续；不把打开 CMD 作为默认解决方法，不自行关闭安全限制。确实无法由宿主执行时才报告具体阻碍，并说明可选的手动备用方式。

## 验证与发布

面向同学的 `start.cmd` 提供中文上手向导，`diagnose.cmd` 默认只做本地诊断；`diagnose.cmd -Online -NoPause` 才会验证一次学校连接，不刷新、不写学习状态。`-Json` 输出不含个人内容的摘要，不自动上传。详见 [START.md](START.md)。新增入口测试随下面的 tests 目录 Python 测试执行。

```powershell
npm test
& '.venv/Scripts/python.exe' -m unittest discover -s tests -p 'test_*.py'
& '.venv/Scripts/python.exe' -m unittest discover -s tools/study -p 'test_*.py'
```

测试使用虚构课程和临时状态，不连接学校，不读取个人 Token。本轮学生上线可用性修复的回归为 `tests/mcp-startup.test.mjs`（真实 stdio 子进程在未登录/空目录启动、16 个工具、结构化错误与同聊天懒恢复），以及 `tests/test_dsh_integration.py` 的安装器状态/退出码与 `tools/onboarding/setup-dsh.ps1` 完成判定用例；受限环境运行 Node 测试前先把 `TEMP`/`TMP` 指向仓库内可写目录。课程级授权与资料同步的新增回归为 `tests/course-library.test.mjs`（Node）和 `tools/study/test_course_library.py`（Python）；专用工具回归为 `tests/chapter-check-mcp.test.mjs`（真实 MCP 协议配合虚构档案，核对严格比较、复用、输入与授权边界、错误脱敏）；按章核对回归为 `tests/chapter-check.test.mjs`（Node）和 `tools/study/test_session.py` 的 `ChapterCheckTests`（Python），本轮覆盖：Topic 5 不误选其他 Topic、日期或 Tutorial 区块；区块序号不被当章节号；匹配不唯一返回候选且不联网；同聊天第二次不联网；新增本章资料增量核对；本地文件改变/新聊天/账号变化/`--force` 使回执失效；失败或缺基线不误报成功且不自动重试；stderr 阶段进度实时可见且 stdout 仍是可解析 JSON。本轮的按需查询回归为 `tests/read-queries.test.mjs`（Node），学习处理回归为 `tools/study/test_question_exposure.py`、`test_material_learning_integration.py`，原答绑定归档回归为 `tools/study/test_evidence_archive_binding.py`，跨校数字 ID 回归分布在 `tests/chat-course-selection.test.mjs`、`tests/course-selection.test.mjs`、`tests/chapter-check.test.mjs`、`tools/study/test_course_library.py` 和 `tests/test_auth.py`。可选 `tests/browser_smoke.py` 只访问本机模拟服务，使用无界面 Edge，不验证学校真实 SSO；真实连接需本人登录后运行 `node tools/moodle/run.mjs check`。

发布前从通用源码文件清单建立提交，检查 `git status`。不得加入 `Study/`、`.private/`、本地配置、依赖环境、个人附件、真实题库或状态备份，不用 `git add -f` 绕过忽略规则。Node 依赖锁在 package-lock.json；Python 固定版本见 requirements.txt。


资料升级、历史引用与经授权的 30 天归档清理见 [MATERIAL_VERSIONS.md](MATERIAL_VERSIONS.md)。默认搜索只读当前版；`context/sync` 可触发已启用的归档维护，实际删除时会更新资料删除标记，学习证据保持不变。


考试来源写入与统一聊天入口共用快照校验。已有较新轮次、范围改变或快照被修改时，旧的 `exam-sync/exam-profile` 会拒绝保存；重读最新独立快照并核读后再提交，不使用旧草稿回退已核验要求。
